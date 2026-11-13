---
name: Compatibility report
about: Tell us whether your laptop works (both results help!)
labels: compatibility
---

**Does it work?**
- [ ] Yes, everything I tried works
- [ ] Partly (explain below)
- [ ] No

**Output of the hardware check**

Run from a clone of the repository:
`./tests/hardware/check_real_hardware.py --yes --stop-service`

```
(paste the "compatibility report" block here)
```

**Things the check can't test**
- Fn brightness keys change the light and the app follows: yes / no / not tried
- Colors come back after suspend/resume: yes / no / not tried
- Tray icon and Ctrl+Super+K work: yes / no / not tried

**Notes**
(Keyboard type, 1 or 3 zones, anything odd.)
