# KB-002: Outlook Troubleshooting

**Symptom: stuck on "Loading profile" or won't open**
1. Fully quit Outlook (check Task Manager for OUTLOOK.EXE).
2. Start in **safe mode**: Win+R → `outlook.exe /safe`. If it opens, a corrupt add-in is likely — disable add-ins and relaunch normally.
3. If safe mode fails, rebuild the mail profile: Control Panel → Mail → Show Profiles → Add new, set as default.
4. Still failing → escalate tier 2 (possible OST corruption or server-side issue).

Never advise reinstalling Windows or Office as a first-line fix.
