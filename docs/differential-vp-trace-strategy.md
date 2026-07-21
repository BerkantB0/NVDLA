# Differential `nv_small` VP Trace Strategy

## Purpose

The differential trace gate checks that the modern Linux 6.6 NVDLA software
stack preserves the externally visible register behavior of the legacy Linux
4.13 stack. Both stacks execute the same pinned LeNet workload on the same
source-built `nv_small` VP binary and CMOD.

The reference trace is valid only when the legacy stack independently produces
the expected LeNet output, completes the expected hardware layers, and reports
no bad kernel or VP patterns. A failing legacy run is classified as
`reference_invalid`; it is never used as a golden trace.

## Observation Boundary

Transactions are captured by the VP SystemC `csb_adaptor`, outside the guest
kernel and KMD. The KMD and UMD patch queue remains unchanged. The VP also
captures DBB traffic for later analysis, but only CSB transactions gate this
milestone.

Each run retains the original SystemC log, split CSB and DBB logs, and a
canonical JSONL CSB stream. Canonical events contain source-independent
interface, operation, offset, length, data, response, and register-name fields.
A future FPGA ILA importer must emit this same schema rather than introducing a
second comparison format.

## Comparison Policy

- Compare ordinary register writes by exact relative offset, value, and order.
- Reject every non-OK TLM response.
- Ignore timestamps and textual log formatting.
- Mask absolute values written to DMA address registers, while requiring the
  same address-register order, valid extmem range, and word alignment.
- Collapse consecutive duplicate status, pointer, and interrupt reads so host
  scheduling and polling frequency do not create false mismatches.
- Keep operation-enable, interrupt-mask, interrupt-status, and interrupt-clear
  transactions strict.

The comparison records the first mismatch with context, mismatch counts,
per-register summaries, output hashes, and completed-layer counts. Its result is
one of `pass`, `reference_invalid`, `trace_mismatch`, `output_mismatch`, or
`runtime_failure`.

## Evidence Limits

A passing differential trace is evidence that the forward port preserves UMD,
DRM ioctl, GEM, scheduler, CSB programming, and interrupt-driven completion
behavior in the register-accurate VP. It does not prove FPGA timing, reset,
physical interrupt routing, or non-coherent HP0 DMA behavior. Later ILA captures
from the real design will be normalized into the same event format and compared
against the validated VP trace.
