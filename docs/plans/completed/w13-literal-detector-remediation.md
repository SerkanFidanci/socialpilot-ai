# W13 literal dedektör düzeltmesi

**Durum:** tamamlandı · **Tarih:** 2026-08-01

## Sonuç

`main`, W13 sıcak dalına alındı. Literal dedektörü yazıyla para/indirim/tarih varyantlarını
yakalar; doğrulanmış slot çözümü değişmedi.

## Değişen dosyalar

- `services/api/app/modules/content/script.py`
- `services/api/tests/unit/test_content_script_unit.py`
- `docs/handoffs/W13-script-generation.md`

## Doğrulama

- Kritik dört eski bypass gerçek HTTP yolunda `422` ile reddedildi.
- `pytest -q` (`RUN_INTEGRATION_TESTS=1`): 628 passed.
- Ruff lint/format ve strict mypy geçti.
