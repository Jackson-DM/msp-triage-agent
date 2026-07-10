# KB-003: VPN & Office Wi-Fi

**VPN client:** SecureLink. Gateway address: **vpn.summitmit.example** (verify this exact address in the client settings — a common failure is a stale/mistyped gateway).

**"Could not connect to gateway" runbook:**
1. Confirm gateway address is vpn.summitmit.example.
2. **Restart the SecureLink client** (quit from system tray, relaunch).
3. Reboot the computer.
4. Test from a phone hotspot to rule out home-router blocking.
5. Still failing → escalate tier 2.

**Office wi-fi slowness (localized):**
1. Confirm device is on the **5 GHz** network (SSID ending -5G), not the 2.4 GHz guest band.
2. Note the affected room(s). If degradation persists after band check or affects one area consistently for 3+ days, reply back — persistent dead zones require a tier-2 site survey.
