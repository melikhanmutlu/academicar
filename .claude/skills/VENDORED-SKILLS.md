# Vendored third-party skills

Cloud sessions load `.claude/skills/` from the clone but do not install plugins
declared in `.claude/settings.json`, so these skills are copied here. Only the
skill folders were copied (no plugin hooks, statusline, or marketplace files).
Each folder keeps its upstream LICENSE. Superpowers is listed separately in
`SUPERPOWERS-ATTRIBUTION.md`.

Project rules in `CLAUDE.md` (DESIGN.md visual language, no PRs, push rules)
take precedence over anything these skills suggest.

| Folder | Source | License | Commit (2026-10-20) | Notes |
| --- | --- | --- | --- | --- |
| `impeccable/` | https://github.com/pbakaus/impeccable (`.claude/skills/impeccable`) | Apache-2.0 | dad8dbb3 | Launcher downloads its engine binary on first run |
| `taste-skill/` | https://github.com/Leonxlnx/taste-skill (`skills/taste-skill`, v2.1.4) | MIT | 6010f91a | Skill name `design-taste-frontend`; scripts need Node |
| `ui-ux-pro-max/`, `design-system/`, `banner-design/`, `slides/` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (`.claude/skills/*`) | MIT | 56ea9cf1 | `${CLAUDE_PLUGIN_ROOT}` changed to `${CLAUDE_PLUGIN_ROOT:-.}` so the search script resolves from the repo root |
| `humanizer/` | https://github.com/blader/humanizer | MIT | 8c307f36 | SKILL.md only |
| `caveman/` | https://github.com/JuliusBrussee/caveman (`plugins/caveman/skills/caveman`, v4.0.1) | Apache-2.0 | bad48a0e | Skill only: off until `/caveman`, no always-on hook |
| `last30days/` | https://github.com/mvanhorn/last30days-skill (`skills/last30days`, v3.28.1) | MIT | 832d0959 | Demo `assets/` dropped; Python 3.12+; API keys optional |

To update one, re-copy the same folder from upstream and bump the commit here.
