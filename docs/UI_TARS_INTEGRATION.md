# UI-TARS Integration

Protocol reference: https://github.com/bytedance/UI-TARS/blob/main/codes/ui_tars/action_parser.py
Local reference checkout: D:/ls/UI-TARS.

TAGENT's adapter independently implements the action protocol with AST literal
parsing. It does not evaluate generated Python. The existing visual agent accepts
UI-TARS responses and converts them into its existing executor actions.

Optional llm_config.json fields:
- action_format: uitars (default remains the existing JSON protocol)
- coordinate_mode: pixels, normalized, or 1000

Coordinates must match the configured model's output convention. Pixel coordinates
refer to the supplied screenshot; normalized coordinates range from zero to one.
The adapter rejects invalid dimensions, out-of-bounds values, and executable expressions.

## Runtime Evidence

2026-09-11: WPS Writer created a blank document and entered the requested text via
the existing visual agent. The model stalled selecting the D drive in Save As.
The operator used the UI-TARS parser to map a normalized click into the filename
field, entered the absolute path, and confirmed saving.

Artifact: runs/writer_20260911_212511/hello.docx
The word/document.xml text was independently verified to equal U+60A8 U+597D.
Screenshots: runs/save_filled.png and runs/save_result.png.
This proves document creation and saving with recovery; it does not prove a fully
autonomous repeat run or delivery of any email. The initial report retains max_steps.

Tests: python/tests/test_uitars_actions.py and python/tests/test_visual_agent.py.

## Follow-up Verification

WPS Save As now has a local UIA handler (services/wps_save.py) using the filename
ComboBox/Edit value and Save button Invoke pattern. The visual worker initializes
UI Automation in its own thread. The rerun saved
runs/writer_20260911_214905/hello.docx automatically and independently verified
the DOCX text. This rerun started from the already open document.

Outlook: one new message was composed using TAGENT's input executors and inspected
before sending. Subject TAGENT-20260911-2155, recipient 947057797@qq.com, body
U+60A8 U+597D. After one Send click, the matching message appeared under Today in
Sent Items at 21:57. Evidence: runs/mail_ready.png and runs/mail_sent_result.png.
This verifies the sender's sent record, not recipient-side delivery. The document
was not attached. This was an operator-observed test, not a fully autonomous
cross-application replay certification.

Attachment correction at 22:24: subject TAGENT-WPS-20260911-2216 was sent to
947057797@qq.com with hello.docx attached (10 KB). Source file:
runs/writer_20260911_214905/hello.docx. The sent message was opened and inspected;
recipient, subject, body, attachment name and size were visible together.
Evidence: runs/attachment_ready.png, runs/attachment_sent.png,
runs/attachment_sent_detail.png. This satisfies the document attachment check.
The legacy keyboard CLI remains text-only; attachment selection was performed
using TAGENT screen input tools under operator observation.

2026-09-12: the CLI now supports --attach and --verify-only. The attachment-check-7
draft was prepared automatically with the target recipient, body and hello.docx.
The --verify-only run opened the existing TAGENT-WPS-20260911-2216 sent message
and verified its recipient, subject, body and attachment without sending again.
Verification artifacts are under runs/outlook_messages/ keyed by recipient/subject.
