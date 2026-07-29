# SocialPilot AI — Mobile Analysis Demo

A Flutter demo client for the media-analysis pipeline: pick a business, upload an
MP4, watch each processing step, then read the scenes, transcript, and
video-understanding results.

This is a development demo. It has no production login, no store packaging, no
payments, no social-network connections, and no real AI provider.

## Layers

```
lib/
  config/        runtime configuration from --dart-define
  models/        wire models and pure display logic
  api/           HTTP transport and error translation
  repositories/  upload coordination, summary reads, polling
  screens/       business list, business create, upload, processing, result
  widgets/       step checklist, coverage card, error banner, formatters
```

## Configuration

No base URL and no token is committed. Both come from `--dart-define`:

| Define | Meaning |
| --- | --- |
| `API_BASE_URL` | e.g. `http://10.0.2.2:8000` for the Android emulator |
| `IDENTITY_TOKEN` | signed development identity token |
| `POLL_INTERVAL_SECONDS` | optional, defaults to 3 |

Without them the app opens a configuration screen instead of failing silently.

On the Android emulator `10.0.2.2` maps to the host. On a physical device use the
host machine's LAN address, never `localhost`.

### Minting a development token

The backend accepts a signed local identity token. Generate one from the running
API container so it uses the same signing key the API was started with:

```bash
docker compose exec -T api python -c "from app.core.config import get_settings; from app.infrastructure.identity.local import LocalIdentityVerifier; print(LocalIdentityVerifier.sign_for_testing(signing_key=get_settings().local_identity_signing_key.get_secret_value(), subject='demo-user', email='demo@example.com'))"
```

Keep the value out of source control; pass it on the command line only.

## Running

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000 --dart-define=IDENTITY_TOKEN=PASTE_TOKEN_HERE
```

## Verification

```bash
flutter pub get
```

```bash
flutter analyze
```

```bash
flutter test
```

## Upload approach

The client follows the backend's direct-upload contract and never sends media
bytes to the API:

1. Stream the file to compute SHA-256 in 1 MiB chunks, so a large video is never
   held in memory.
2. `POST /media/uploads` with the declared type, size, checksum, and part count.
3. `PUT` each 8 MiB range straight to the returned object-storage part URL with a
   streamed request body, reporting cumulative byte progress. No bearer token is
   attached — the part URL carries its own grant.
4. `POST /media/uploads/{id}/complete` with the part ETags and the checksum.

The picker returns a path rather than bytes, and every read is a stream, so the
video is never buffered whole. The upload button stays disabled for the entire
in-flight window, so a second tap cannot start a duplicate upload, and each
attempt carries its own idempotency key.

> The development backend ships a byte-free in-memory storage fake whose part URLs
> point at the unroutable `fake-storage.invalid` host. Steps 1, 2 and 4 exercise the
> real API, but step 3 cannot transfer bytes until a real object-storage adapter
> (MinIO, per ADR-002) is configured. The app reports that failure plainly rather
> than pretending the upload succeeded.

## Polling approach

The processing screen polls
`GET /businesses/{id}/media/{asset_id}/processing-summary` on a fixed interval.
Requests never overlap. Polling stops as soon as the reported step is terminal
(`completed` or `failed`), on a permanent error, or after a bounded number of
consecutive transport failures. A step this build does not recognize is never
treated as terminal, so a newer backend cannot strand the client. `dispose`
cancels the timer and closes the stream, so no timer outlives its screen.
