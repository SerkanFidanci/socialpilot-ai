"""The FFmpeg render adapter: one implementation of `RenderPort`, not the port itself.

Everything FFmpeg-specific lives below this line — command construction, filter graphs, the
concat demuxer, subtitle formats. The domain sees a `RenderPlan` in and a `RenderResult` out,
which is what keeps a managed render service viable as a second adapter (ADR-013, STATUS K5).

Three safety properties are worth stating because they are easy to lose in a rewrite:

**Text never enters a command string.** Overlay text can be a tenant's literal or a value
copied out of a campaign record, and `drawtext` has its own expression language: a colon
changes option parsing and `%{...}` is expanded at draw time. Text is therefore written to a
file and referenced with `textfile=` plus `expansion=none`, so the bytes are drawn and never
parsed. Captions take the same route through a generated ASS file.

**Every subprocess is bounded and quiet.** Commands run with `shell=False`, a timeout, and
their diagnostics redirected to a private temporary file whose *size* is checked and whose
*contents* are never read — FFmpeg echoes input paths and metadata into stderr, and none of
that belongs in a log line, an error body, or a span.

**Partial output never survives.** Any failure removes what the run created, so the scratch
budget in `worker/scratch.py` measures only live work.

The render is four bounded stages: normalize each cut to identical parameters, concatenate and
draw over the result, downscale to a preview, and pull a thumbnail. Stage one exists so that
stage two's concat demuxer can stream-copy rather than re-decode every source twice.

Slice 2E adds speech, which slice 2C produced and no adapter could use. Voiceover lines are
joined into one uniform track and mixed under the footage's own sound, with the bed ducked when
the timeline asks for it (§18.2's `duck_under_voice`, §19.1's "music ducking" applied to the one
bed that exists today). The mix runs inside `filter_complex` rather than through `-af` because a
sidechain compressor needs both signals in the same graph; a timeline that places no speech takes
exactly the path it took before, mapping `0:a` and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile

from app.core.config import Settings
from app.modules.content.render import (
    PlannedCaption,
    PlannedText,
    ProvenanceState,
    RenderCapabilities,
    RenderedArtifact,
    RenderPermanentError,
    RenderPlan,
    RenderPort,
    RenderProfile,
    RenderProfileSpec,
    RenderRequest,
    RenderResult,
    RenderSummary,
    RenderTransientError,
    profile_spec,
)
from app.modules.content.timeline import (
    AudioTrackKind,
    CaptionSource,
    CropMode,
    OverlayAnchor,
    TextStyle,
    TransitionKind,
)

_STDERR_LIMIT = 16_384
_PROBE_STDOUT_LIMIT = 65_536
# The font path is interpolated into a filter option, so anything that could terminate the
# option or open a quote is refused before a command is built rather than escaped and hoped for.
_FILTER_SAFE_PATH = re.compile(r"^[A-Za-z0-9_\-./]+$")
# ASS colours are &HAABBGGRR. The style registry is closed, so a small table is total.
_ASS_COLOURS = {
    "white": "&H00FFFFFF",
    "black": "&H00000000",
    "yellow": "&H0000FFFF",
    "red": "&H000000FF",
}


@dataclass(frozen=True, slots=True)
class _Box:
    """The pixel rectangle overlays may occupy."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


class FFmpegRenderAdapter(RenderPort):
    """Render a plan with a fixed FFmpeg binary, one bounded subprocess at a time."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def capabilities(self) -> RenderCapabilities:
        """What this adapter can do today.

        `voiceover` joins `original` in slice 2E: speech is joined, gained and mixed under the
        footage below. `fade` and `music` stay absent on purpose — a crossfade is a real filter
        nobody has written and music needs a licence record (§18.3) before a track may be laid
        at all. Declaring either here would turn a clean validation rejection into a job that
        fails halfway through a render.
        """

        return RenderCapabilities(
            profiles=frozenset(RenderProfile),
            crop_modes=frozenset(CropMode),
            transitions=frozenset({TransitionKind.CUT}),
            audio_sources=frozenset({AudioTrackKind.ORIGINAL, AudioTrackKind.VOICEOVER}),
            caption_sources=frozenset({CaptionSource.TRANSCRIPT}),
            max_duration_ms=self._settings.render_max_duration_ms,
            max_video_tracks=1,
            supports_provenance_manifest=False,
        )

    async def render(self, *, request: RenderRequest) -> RenderResult:
        workdir = _controlled_directory(request.workdir)
        spec = profile_spec(request.plan.profile)
        created: list[Path] = []
        try:
            segments = await self._normalize_segments(request.plan, spec, workdir, created)
            voice = await self._join_voiceover(request.plan, workdir, created)
            master = await self._compose(request.plan, spec, segments, voice, workdir, created)
            preview = await self._downscale(master, request.preview_profile, workdir, created)
            thumbnail = await self._thumbnail(master, workdir, created)
            summary = await self._probe(master, workdir)
            return RenderResult(
                master=self._artifact("master", master, "video/mp4"),
                preview=self._artifact("preview", preview, "video/mp4"),
                thumbnail=self._artifact("thumbnail", thumbnail, "image/jpeg"),
                summary=summary,
                # Re-encoding discards any C2PA manifest the sources carried, and this adapter
                # cannot sign a replacement. Saying so leaves a queryable set of outputs for a
                # future signing step instead of a silent gap in provenance.
                provenance=ProvenanceState.STRIPPED_PENDING_REATTACH,
            )
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    # --- stage 1: one uniform clip per segment -------------------------------------------

    async def _normalize_segments(
        self, plan: RenderPlan, spec: RenderProfileSpec, workdir: Path, created: list[Path]
    ) -> list[Path]:
        """Cut and reshape each source to identical parameters so concat can stream-copy."""

        outputs: list[Path] = []
        for index, segment in enumerate(plan.segments):
            output = workdir / f"segment-{index:03d}.mp4"
            created.append(output)
            source = _controlled_source(segment.source_path)
            command = [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                # Seeking before -i is the fast path; the re-encode below makes it frame-exact.
                "-ss",
                _seconds(segment.source_start_ms),
                "-t",
                _seconds(segment.duration_ms),
                "-i",
                str(source),
            ]
            if not segment.has_audio:
                # Every segment must carry an audio stream or the concat demuxer refuses the
                # set. Silence is generated rather than audio being dropped, because dropping
                # it would also silence the segments that do have sound.
                command += [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            command += [
                "-filter_complex",
                _fit_filter(segment.crop_mode, width=spec.width, height=spec.height, fps=spec.fps),
                "-map",
                "[vseg]",
                "-map",
                "0:a:0" if segment.has_audio else "1:a:0",
                *self._video_encoding(),
                *_AUDIO_ENCODING,
                "-shortest",
                str(output),
            ]
            await self._run(command, workdir, "RENDER_SEGMENT_FAILED")
            _require_output(output)
            outputs.append(output)
        return outputs

    # --- stage 1b: one uniform speech track ------------------------------------------------

    async def _join_voiceover(
        self, plan: RenderPlan, workdir: Path, created: list[Path]
    ) -> Path | None:
        """Join the voiceover's lines into a single track, or return `None` when there is none.

        Slice 2C stores one object per script line, so speech arrives as an ordered set of files
        rather than one. They are concatenated through the filter graph rather than the concat
        demuxer because the demuxer requires identical stream parameters and a provider is under
        no obligation to return them; `aformat` makes that true instead of assuming it.

        The line count is bounded here as well as upstream. A cap that only exists in the domain
        is a cap the adapter is trusting someone else to have applied, and the filter string
        below grows with it.
        """

        voiceover = plan.audio.voiceover
        if voiceover is None or not voiceover.segment_paths:
            return None
        if len(voiceover.segment_paths) > _MAX_VOICE_INPUTS:
            raise RenderPermanentError("RENDER_VOICEOVER_UNSUPPORTED")
        output = workdir / "voice.m4a"
        created.append(output)
        command = [
            self._settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        for path in voiceover.segment_paths:
            command += ["-i", str(_controlled_source(path))]
        labels = "".join(f"[a{index}]" for index in range(len(voiceover.segment_paths)))
        chain = [
            f"[{index}:a]{_AUDIO_FORMAT}[a{index}]" for index in range(len(voiceover.segment_paths))
        ]
        chain.append(f"{labels}concat=n={len(voiceover.segment_paths)}:v=0:a=1[voice]")
        command += [
            "-filter_complex",
            ";".join(chain),
            "-map",
            "[voice]",
            *_AUDIO_ENCODING,
            str(output),
        ]
        await self._run(command, workdir, "RENDER_VOICEOVER_FAILED")
        _require_output(output)
        return output

    # --- stage 2: concat, overlay, burn captions -----------------------------------------

    async def _compose(
        self,
        plan: RenderPlan,
        spec: RenderProfileSpec,
        segments: list[Path],
        voice: Path | None,
        workdir: Path,
        created: list[Path],
    ) -> Path:
        box = _Box(*spec.safe_area.box(width=spec.width, height=spec.height))
        master = workdir / "master.mp4"
        created.append(master)

        listing = workdir / "segments.txt"
        created.append(listing)
        # Names are generated above (`segment-000.mp4`), so the concat list holds no path a
        # caller influenced and the demuxer runs relative to the private workdir.
        listing.write_text("".join(f"file '{path.name}'\n" for path in segments), encoding="utf-8")

        command = [
            self._settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            listing.name,
        ]
        for logo in plan.logos:
            command += ["-i", str(_controlled_source(logo.source_path))]
        # The speech track goes in *after* the logos so the logo input indices stay `offset + 1`
        # and the filter graph built below does not have to know whether speech exists.
        voice_index = len(plan.logos) + 1
        if voice is not None:
            command += ["-i", str(_controlled_source(voice))]

        chain, video_label = self._filter_chain(plan, spec, box, workdir, created)
        audio_chain, audio_label = _audio_chain(plan, voice_index=voice_index if voice else None)
        if chain or audio_chain:
            command += ["-filter_complex", ";".join([*chain, *audio_chain])]
        command += ["-map", video_label, "-map", audio_label]
        if audio_label == "0:a" and plan.audio.gain_db != 0:
            # An integer decibel from a bounded schema field, so the expression cannot carry
            # anything but a number into the filter. Only reachable without speech; the mix
            # below applies the same gain inside the graph.
            command += ["-af", f"volume={plan.audio.gain_db}dB"]
        command += [
            *self._video_encoding(),
            *_AUDIO_ENCODING,
            "-movflags",
            "+faststart",
            str(master),
        ]
        await self._run(command, workdir, "RENDER_COMPOSE_FAILED")
        _require_output(master)
        return master

    def _filter_chain(
        self,
        plan: RenderPlan,
        spec: RenderProfileSpec,
        box: _Box,
        workdir: Path,
        created: list[Path],
    ) -> tuple[list[str], str]:
        """Build the overlay graph. Returns the graph and the label to map as video."""

        chain: list[str] = []
        label = "[0:v]"
        stage = 0

        for offset, logo in enumerate(plan.logos):
            # The concat input is 0; logo inputs follow it in the order they were appended.
            source_index = offset + 1
            width = max(2, round(spec.width * logo.width_ratio))
            chain.append(f"[{source_index}:v]scale={width}:-2[logo{offset}]")
            x, y = _overlay_position(logo.anchor, box, width_expression="w", height_expression="h")
            chain.append(
                f"{label}[logo{offset}]overlay={x}:{y}:"
                f"enable='between(t,{_seconds(logo.start_ms)},{_seconds(logo.end_ms)})'[v{stage}]"
            )
            label, stage = f"[v{stage}]", stage + 1

        for index, text in enumerate(plan.texts):
            text_file = workdir / f"text-{index:03d}.txt"
            created.append(text_file)
            # Written verbatim and read by FFmpeg as data. `expansion=none` in the filter is
            # what stops `%{...}` inside a tenant's own string being evaluated at draw time.
            text_file.write_text(text.text, encoding="utf-8")
            chain.append(f"{label}{self._drawtext(text, text_file.name, spec, box)}[v{stage}]")
            label, stage = f"[v{stage}]", stage + 1

        if plan.captions:
            subtitles = workdir / "captions.ass"
            created.append(subtitles)
            subtitles.write_text(
                _ass_document(
                    plan.captions,
                    style=plan.caption_style,
                    font_family=self._settings.render_font_family,
                    spec=spec,
                    box=box,
                ),
                encoding="utf-8",
            )
            chain.append(f"{label}ass={subtitles.name}[v{stage}]")
            label, stage = f"[v{stage}]", stage + 1

        if not chain:
            # `-map` needs a filter-graph output label; with an empty graph, map the input.
            return ([], "0:v")
        return (chain, label)

    def _drawtext(
        self, text: PlannedText, filename: str, spec: RenderProfileSpec, box: _Box
    ) -> str:
        font = self._settings.render_font_file
        if not _FILTER_SAFE_PATH.fullmatch(font):
            raise RenderPermanentError("RENDER_FONT_PATH_INVALID")
        font_px = max(1, round(spec.height * text.style.font_height_ratio))
        x, y = _overlay_position(
            text.anchor, box, width_expression="text_w", height_expression="text_h"
        )
        return (
            f"drawtext=fontfile={font}:textfile={filename}:expansion=none:"
            f"fontsize={font_px}:fontcolor={text.style.colour}:"
            f"bordercolor={text.style.border_colour}:borderw={text.style.border_width}:"
            f"x={x}:y={y}:enable='between(t,{_seconds(text.start_ms)},{_seconds(text.end_ms)})'"
        )

    # --- stage 3: preview, thumbnail, probe ----------------------------------------------

    async def _downscale(
        self, master: Path, profile: RenderProfile, workdir: Path, created: list[Path]
    ) -> Path:
        spec = profile_spec(profile)
        preview = workdir / "preview.mp4"
        created.append(preview)
        await self._run(
            [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                master.name,
                "-vf",
                f"scale={spec.width}:{spec.height}:"
                "force_original_aspect_ratio=decrease:force_divisible_by=2",
                *self._video_encoding(),
                *_AUDIO_ENCODING,
                "-movflags",
                "+faststart",
                str(preview),
            ],
            workdir,
            "RENDER_PREVIEW_FAILED",
        )
        _require_output(preview)
        return preview

    async def _thumbnail(self, master: Path, workdir: Path, created: list[Path]) -> Path:
        thumbnail = workdir / "thumbnail.jpg"
        created.append(thumbnail)
        await self._run(
            [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                master.name,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(thumbnail),
            ],
            workdir,
            "RENDER_THUMBNAIL_FAILED",
        )
        _require_output(thumbnail)
        return thumbnail

    async def _probe(self, master: Path, workdir: Path) -> RenderSummary:
        """Describe the output from the file itself rather than from what was requested."""

        result = await self._run(
            [
                self._settings.ffprobe_binary,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                master.name,
            ],
            workdir,
            "RENDER_OUTPUT_INVALID",
            capture_stdout=True,
        )
        stdout = result.stdout or ""
        if len(stdout) > _PROBE_STDOUT_LIMIT:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID")
        try:
            payload = json.loads(stdout)
            streams = list(payload["streams"])[:8]
            video = next(item for item in streams if item.get("codec_type") == "video")
            audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
            duration_ms = round(float(payload["format"]["duration"]) * 1000)
            width, height = int(video["width"]), int(video["height"])
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as error:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID") from error
        if duration_ms < 1 or width < 1 or height < 1:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID")
        return RenderSummary(
            duration_ms=duration_ms,
            width=width,
            height=height,
            video_codec=str(video.get("codec_name", ""))[:32],
            audio_codec=None if audio is None else str(audio.get("codec_name", ""))[:32],
        )

    # --- process plumbing ------------------------------------------------------------------

    def _video_encoding(self) -> list[str]:
        return [
            "-c:v",
            "libx264",
            "-preset",
            self._settings.render_x264_preset,
            "-pix_fmt",
            "yuv420p",
        ]

    async def _run(
        self,
        command: list[str],
        workdir: Path,
        error_code: str,
        *,
        capture_stdout: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result, stderr_size = await asyncio.to_thread(
                _run_with_bounded_diagnostics,
                command,
                workdir,
                self._settings.render_step_timeout_seconds,
                capture_stdout,
            )
        except subprocess.TimeoutExpired as error:
            raise RenderTransientError("RENDER_TIMEOUT") from error
        except OSError as error:
            raise RenderTransientError("RENDER_UNAVAILABLE") from error
        if stderr_size > _STDERR_LIMIT:
            raise RenderPermanentError("RENDER_DIAGNOSTIC_LIMIT_EXCEEDED")
        if result.returncode != 0:
            raise RenderPermanentError(error_code)
        return result

    def _artifact(self, kind: str, path: Path, content_type: str) -> RenderedArtifact:
        try:
            byte_size = path.stat().st_size
        except OSError as error:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID") from error
        if byte_size <= 0:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID")
        if byte_size > self._settings.render_max_output_bytes:
            raise RenderPermanentError("RENDER_OUTPUT_SIZE_EXCEEDED")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1_048_576):
                    digest.update(chunk)
        except OSError as error:
            raise RenderPermanentError("RENDER_OUTPUT_INVALID") from error
        return RenderedArtifact(
            kind=kind,
            path=path,
            content_type=content_type,
            byte_size=byte_size,
            sha256_checksum=digest.hexdigest(),
        )


_AUDIO_ENCODING = ("-c:a", "aac", "-ar", "48000", "-ac", "2")
# Every audio stream entering a mix is forced to one shape first. `sidechaincompress` and `amix`
# both require their inputs to agree, and a synthesized line arrives in whatever the provider
# chose to write.
_AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
# Speech lines per render. Slice 2C caps a script at eight; this is the adapter's own bound,
# because a cap that lives only in the domain is one the adapter is trusting a caller to apply.
_MAX_VOICE_INPUTS = 16
# The ducking curve. These are product judgements, not platform facts: the bed drops when speech
# crosses the threshold and recovers over a third of a second, which is slow enough not to pump
# between words and fast enough to come back inside a pause.
_DUCK_FILTER = "sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350"
# A hard ceiling after the mix. `amix` with `normalize=0` keeps both signals at their intended
# level, which is what makes the gains in the timeline mean something — and which can sum above
# full scale. The limiter is what stops that becoming the clipping §18.3 asks about.
_MIX_LIMITER = "alimiter=limit=0.95"


# --- module-level helpers -------------------------------------------------------------------


def _audio_chain(plan: RenderPlan, *, voice_index: int | None) -> tuple[list[str], str]:
    """Build the audio graph. Returns the graph and the label to map as audio.

    With no speech the graph is empty and `0:a` is mapped directly, so a timeline that placed no
    voiceover renders through exactly the path it did before slice 2E — including its `-af` gain.

    With speech the bed and the voice are formatted alike, gained, optionally ducked, mixed
    without `amix`'s normalization (so the timeline's decibels survive) and limited. `duration`
    is `first`, meaning the bed: speech shorter than the footage leaves silence at the end rather
    than truncating the video, and speech longer than the footage cannot occur because §18.3
    refuses that timeline before a render starts.
    """

    if voice_index is None:
        return ([], "0:a")
    voice_gain = plan.audio.voiceover.gain_db if plan.audio.voiceover is not None else 0
    chain = [
        f"[0:a]{_AUDIO_FORMAT},volume={plan.audio.gain_db}dB[bed]",
        f"[{voice_index}:a]{_AUDIO_FORMAT},volume={voice_gain}dB[voice]",
    ]
    if plan.audio.duck_under_voice:
        chain += [
            # The key has to be a *copy* of the voice: the same stream cannot be both the
            # sidechain input and a mix input.
            "[voice]asplit=2[voicemix][voicekey]",
            f"[bed][voicekey]{_DUCK_FILTER}[bedducked]",
            "[bedducked][voicemix]amix=inputs=2:duration=first:dropout_transition=0:"
            f"normalize=0,{_MIX_LIMITER}[aout]",
        ]
    else:
        chain.append(
            "[bed][voice]amix=inputs=2:duration=first:dropout_transition=0:"
            f"normalize=0,{_MIX_LIMITER}[aout]"
        )
    return (chain, "[aout]")


def _run_with_bounded_diagnostics(
    command: list[str], workdir: Path, timeout_seconds: int, capture_stdout: bool
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run without retaining diagnostics in memory or in any error the caller can see.

    FFmpeg writes input paths, container metadata and filter descriptions to stderr. The stream
    goes to a private temporary file, only its size is inspected, and neither the contents nor
    the path reach a caller — matching the frame-extraction adapter.
    """

    with TemporaryFile(mode="w+b", dir=workdir) as stderr_file:
        result = subprocess.run(
            command,
            cwd=workdir,
            shell=False,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return result, stderr_file.tell()


def _controlled_directory(workdir: Path) -> Path:
    if workdir.is_symlink() or not workdir.is_dir():
        raise RenderPermanentError("RENDER_WORKDIR_INVALID")
    return workdir.resolve(strict=True)


def _controlled_source(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise RenderPermanentError("RENDER_SOURCE_INVALID")
    return path.resolve(strict=True)


def _require_output(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RenderPermanentError("RENDER_OUTPUT_INVALID")


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def _fit_filter(crop_mode: CropMode, *, width: int, height: int, fps: int) -> str:
    """Fit an arbitrary source into the target frame, always ending at the `[vseg]` label.

    A uniform output label lets the caller map the result the same way for every crop mode,
    including the blurred-background case, which needs a split and cannot be a plain chain.
    """

    common = f"fps={fps},setsar=1"
    if crop_mode is CropMode.SMART_COVER:
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{common}[vseg]"
        )
    if crop_mode is CropMode.CONTAIN:
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,{common}[vseg]"
        )
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=20:2[bgblur];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgfit];"
        f"[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2,{common}[vseg]"
    )


def _overlay_position(
    anchor: OverlayAnchor, box: _Box, *, width_expression: str, height_expression: str
) -> tuple[str, str]:
    """Place a box inside the safe rectangle for one of the nine anchors.

    The width/height expressions differ per filter — `drawtext` measures the drawn string as
    `text_w`/`text_h`, `overlay` reports the overlaid image as `w`/`h` — so the caller supplies
    them and the nine-cell arithmetic stays in one place. FFmpeg evaluates them at draw time,
    which makes the final placement exact even though validation only estimated the extent.
    """

    vertical, horizontal = anchor.value.split("_", 1)
    x = {
        "left": f"{box.x0}",
        "center": f"{box.x0}+({box.width}-{width_expression})/2",
        "right": f"{box.x1}-{width_expression}",
    }[horizontal]
    y = {
        "top": f"{box.y0}",
        "middle": f"{box.y0}+({box.height}-{height_expression})/2",
        "bottom": f"{box.y1}-{height_expression}",
    }[vertical]
    return (x, y)


def _ass_document(
    captions: tuple[PlannedCaption, ...],
    *,
    style: TextStyle,
    font_family: str,
    spec: RenderProfileSpec,
    box: _Box,
) -> str:
    """Build a self-contained ASS subtitle file.

    ASS carries its own styling, which is why it is preferred over SRT plus a `force_style`
    filter option: the style never has to survive the filter-string parser, so a font family
    containing a space or a comma cannot break the command. Caption text is sanitized for the
    two ASS metacharacters and then written as data.
    """

    font_px = max(1, round(spec.height * style.font_height_ratio))
    primary = _ASS_COLOURS.get(style.colour, _ASS_COLOURS["white"])
    outline = _ASS_COLOURS.get(style.border_colour, _ASS_COLOURS["black"])
    # Alignment 2 is bottom-centre; the vertical margin lifts the line to the safe-area edge.
    margin_v = max(0, spec.height - box.y1)
    margin_h = max(0, box.x0)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {spec.width}\n"
        f"PlayResY: {spec.height}\n"
        # WrapStyle 0 is libass's balanced word wrapping. Captions come from a transcript and
        # can be any length, so they have to break rather than run off the frame; the margins
        # below keep the wrapped block inside the same safe area drawn text obeys.
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_family},{font_px},{primary},{primary},{outline},{outline},"
        f"0,0,0,0,100,100,0,0,1,{style.border_width},0,2,{margin_h},{margin_h},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(caption.start_ms)},{_ass_time(caption.end_ms)},"
        f"Default,,0,0,0,,{_ass_text(caption.text)}\n"
        for caption in captions
    )
    return header + events


def _ass_text(value: str) -> str:
    """Strip the characters ASS treats as markup, then flatten to one line.

    `{` and `}` open an override block and a backslash starts an escape; removing them means a
    caption is drawn exactly as stored, including every Turkish glyph. Newlines are collapsed
    because line wrapping is the renderer's decision, not the transcript's.
    """

    cleaned = value.replace("{", "").replace("}", "").replace("\\", "/")
    return " ".join(cleaned.split())


def _ass_time(milliseconds: int) -> str:
    total_seconds, hundredths = divmod(max(0, milliseconds) // 10, 100)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{hundredths:02d}"
