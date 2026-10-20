# Code review findings for LuxenOS

The repository was reviewed for build, install, and login-path correctness. These are the main issues found during static review and should be tracked before a broader release push.

## 1. Login path is fragile: greetd is enabled without a guaranteed startup path
Severity: High
Location: `config/includes.chroot/usr/libexec/luxenos/enable-greetd.sh`

The installed-system cleanup step enables `greetd.service` but does not run `systemctl daemon-reload` and does not explicitly start the service. In the installed system this is a brittle path: it relies on systemd already seeing the service file and the unit state being correct after the install job runs.

Impact: a successful install can still land at a tty console or never reach the graphical login screen.

Suggested fix: run `systemctl daemon-reload`, then `systemctl enable --now greetd.service`, and fail the install step if the service cannot reach a healthy state.

## 2. The desktop autostart is hard-coded to /dev/tty1
Severity: Medium
Location: `config/includes.chroot/etc/profile.d/luxenos-session.sh`

The script only starts Sway when `tty` equals `/dev/tty1`:

```sh
[ "$(tty 2>/dev/null)" = "/dev/tty1" ]
```

This makes the installed desktop startup depend on a single console name. Any change in hardware, console remapping, or a nonstandard boot path can leave the machine with a valid user but no graphical session.

Impact: users may log in but never get the desktop, which matches the “stuck on the password screen” class of symptoms.

Suggested fix: rely on `systemd` login manager/session hooks or a less brittle detection strategy than a hard-coded tty1 requirement.

## 3. The live USB root-access script trusts the boot parameter too much
Severity: High
Location: `config/includes.chroot/usr/libexec/luxenos/luxenos-live-access`

The script reads `username=*` from `/proc/cmdline` and writes a passwordless sudo and polkit rule for that user. There is no strong validation that the user is the real `live-config` user and not an attacker-supplied or malformed boot argument.

Impact: a malicious or accidental boot parameter can grant the wrong account administrative rights on the live USB.

Suggested fix: validate the username against the expected live account and refuse anything unknown or malformed before writing sudo/polkit rules.

## 4. Release artifact validation is weak and a build can succeed without a usable ISO
Severity: Medium
Location: `.github/workflows/build-iso.yml`

The workflow creates the artifact and then checks whether an ISO exists, but it does not validate that the file is complete before upload. The earlier debug step is best-effort, and the upload step trusts the build output without a final integrity pass.

Impact: CI can upload a truncated or missing artifact, leaving users with a broken release image.

Suggested fix: add explicit checks for the generated ISO and related files before upload, and fail the workflow if the artifact is incomplete or missing.

## 5. The project is explicitly shipping with a known login blocker
Severity: Medium
Location: `README.md`

The top of the README says: `## Don't use see issue #13`.

This is effectively a release blocker being documented in the project itself. It tells users not to use the image because it is already known to be broken in the login path.

Impact: users are being directed to a project state that is not production-ready.

Suggested fix: resolve issue #13 before publishing guidance or release images, or clearly mark the project as alpha/beta until the login flow works reliably.

## 6. The installer and installed-system flows are not fully validated in real installs
Severity: Medium
Location: `README.md`, `config/includes.chroot/etc/calamares/settings.conf`, `config/includes.chroot/usr/libexec/luxenos/luxenos-finish-install`

The project itself states that the installer-to-installed conversion has not been exercised in a real installation yet. That means several handoff points (live session -> Calamares -> installed system -> greetd) are not proven in a real-world install.

Impact: the repo is relying on a not-yet-validated path for the primary user experience.

Suggested fix: add an end-to-end install smoke test in CI or a clear beta label until the real install path is proven to work.

## Review summary

The repository is ambitious and well-organized, but the login and install handoff paths are still the highest-risk area. The most urgent issue is the brittle login flow around greetd and the live-session startup logic, because that is exactly where a user-facing “stuck on the password screen” symptom would appear.
