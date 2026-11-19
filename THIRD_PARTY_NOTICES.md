# Third-party component: DailybotHQ/herdr-peers

This skill includes the complete upstream skill runtime and supporting resources from
DailybotHQ/herdr-peers, version 2.2.1, commit
`6b9f17d8ebffe54ecde68c3bc7c768032be60e92`.

- Source repository: https://github.com/DailybotHQ/herdr-peers
- Component directory: `vendor/herdr-peers/`
- License: [MIT](vendor/herdr-peers/LICENSE)
- Copyright (c) 2026 DailybotHQ contributors
- File-by-file upstream and bundled SHA-256 values: [UPSTREAM.json](vendor/herdr-peers/UPSTREAM.json)

Local packaging changes:

1. Rename upstream `SKILL.md` to `UPSTREAM_GUIDE.md` so only the combined skill has a discoverable entrypoint.
2. Change the helper's `--skill` reader to that renamed file. Message protocol, transport, reply grants, and other upstream runtime behavior are unchanged.

The upstream guide remains reference material. Use this package's `SKILL.md` and
`references/communication.md` for the integrated workflow. Separate upstream install instructions
do not apply. Advanced upstream features are included as code, not enabled by default.

For maintenance, review and update the pinned component deliberately, preserve its MIT notice,
refresh `UPSTREAM.json`, and rerun isolated protocol and framework tests. Do not overwrite the
vendor directory with an unchecked latest release.
