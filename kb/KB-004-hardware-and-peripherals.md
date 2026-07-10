# KB-004: Hardware & Peripherals

**Printer shows Offline for multiple users:**
1. Power-cycle the printer (off 30 seconds).
2. On one affected PC, restart the **print spooler** service (services.msc → Print Spooler → Restart).
3. Confirm correct default printer.
4. Still offline for the office → escalate tier 2 (print server / network).
Never advise purchasing new hardware.

**Docked monitor not detected:**
1. **Reseat the dock** connection to the laptop and the monitor cable at both ends.
2. Win+P → Extend; Settings → Display → Detect.
3. Persisting → dock firmware update or swap test → tier 2 if unresolved.

**Microphone not found in Teams:**
1. Windows Settings → Privacy → check **microphone privacy settings** allow desktop apps.
2. Teams → Devices → select the correct microphone.
3. Test in another app; if dead everywhere, treat as failed peripheral.

**Recurring blue screens / daily crashes:** not a runbook item — escalate tier 2 for hardware diagnostics. Flag data-loss risk in the handoff.
