# Local NVDLA Diagnostic Patch Queue

These patches are applied only by explicit diagnostic build targets. They are
not part of the upstreamable production queue in `patches/nvdla-sw/`.

`0001` adds opt-in task-buffer tracing. `0002` brackets each KMD CSB `readl()`
with offset and return-value messages. If a hardware access never returns, the
last `begin` message identifies the exact register boundary.

Build the standalone PetaLinux module with:

```sh
make petalinux-kmod-diagnostic
```

The resulting `opendla-diagnostic.ko` is archived under `artifacts/`; it is not
included in `petalinux-image-minimal` and must not be counted as production
correctness evidence.
