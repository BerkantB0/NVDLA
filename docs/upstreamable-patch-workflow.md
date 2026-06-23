# Upstreamable NVDLA Patch Workflow

Driver/runtime changes intended for a future `nvdla/sw` fork live as a patch
queue under `patches/nvdla-sw/`. This keeps the dissertation harness separate
from the upstreamable code.

## Commands

```sh
make sources
make patch-apply
make patch-status
make patch-check
```

Edit only `.work/nvdla-sw-patched` while developing upstreamable changes. Commit
those edits inside that worktree with upstream-style messages, then regenerate
the patch queue:

```sh
make patch-format
```

## Rules

- Do not edit `.external/sources/nvdla-sw`; it is the pristine upstream base.
- Do not put PetaLinux paths, XSA addresses, or ZCU102-specific assumptions in
  upstreamable patches.
- Keep ioctl structs and numbers unchanged unless a test proves an ABI change is
  unavoidable.
- Put local-only integration in this repository: recipes, VP scripts, DTS
  snippets, artifacts, and dissertation notes.
- Run `make abi-check` after patch changes; it applies the queue and checks the
  patched source tree.

## Publishing Later

To maintain a fork, apply `patches/nvdla-sw/*.patch` onto a branch starting at
the pinned upstream base, then push that branch. Add your own `Signed-off-by`
lines before publication if required by your chosen contribution process.

