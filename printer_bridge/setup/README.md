# PT-210 local setup record

Historical installation/test log. Current operation: ../README.md. Dithering comparison and reconstructed selection rationale: ../../docs/current/PRINT_DITHERING_DECISION.md. Pending physical confirmations below are not treated as completed.

Installed 2026-11-01 on Apple Silicon macOS 26.5.1.

- User printer: `PT210_IPP`
- Internal queue: `PT210_USB_BRIDGE`
- USB URI: `usb://YICHIP/-Printer%20demo?serial=000000000004`
- Runtime: `~/Library/Application Support/PT210`
- LaunchAgent: `~/Library/LaunchAgents/local.pt210-ipp.plist`
- Logs: `/tmp/pt210-ipp.out.log`, `/tmp/pt210-ipp.err.log`

Based on https://github.com/pier-astro/thermal-printer-driver (GPL-3.0).
Upstream source was inspected before installation. Runtime changes pin the bundled
Poppler executable and local Fontconfig configuration. PDF conversion now completes
to a nonempty temporary file before submission, avoiding empty jobs on conversion failure.
The runtime currently depends on the Codex bundled Poppler path; replace it with a
stable independently installed Poppler path before standalone event deployment.

Connection test submitted once through `PT210_IPP` (job 1). CUPS queues drained
without a reported error. Physical paper output is not yet confirmed by the user.
Do not resend automatically or describe queue completion as verified paper output.

Remaining: Korean/QR test, real saved receipt test, four-method dithering comparison.

## Physical test progress

- User confirmed initial connection test printed successfully.
- Korean text and homepage QR raster test (bridge job 3): user confirmed success.
- Four-method photo comparison (bridge job 4) and archived PASS receipt (bridge job 5): submitted once each; awaiting physical confirmation.
- Prepared files: `output/pt210-test/`.
- Rebuild previews/raw bytes with the bundled Python interpreter and `printer_bridge/prepare_test.py`; this script does not print.
- Dithering uses production `web/src/utils/thermal.ts`, at 360 photo dots inside a 384-dot-wide page, brightness/contrast both 1.0.
- Archived receipt source: user-supplied October 19 PDF. Missing historical fields are not invented. QR diagnostic links to the homepage, not an individual public receipt.

## Automatic printing implementation (2026-11-01)

Final Atkinson layout (test labels removed, token figures emphasized) submitted once as bridge job 6; physical confirmation pending.
Automatic queue migration prepared but NOT applied to Supabase. Render/Vercel NOT deployed or reconfigured by this work.
Fresh temporary environment using server/requirements.txt: all 37 server tests and 7 worker tests passed.
Frontend build and thermal/receipt-print/evaluation-client tests passed. PostgreSQL and PL/pgSQL syntax parser accepted the migration; live DB execution is still pending user application.
Local existing .venv retains its older Gemini SDK; four pre-existing model-configuration test errors occur there. It was not upgraded. The independent print worker only needs Pillow and works with the local .venv.
