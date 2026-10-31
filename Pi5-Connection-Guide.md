# CLAW Raspberry Pi 5: SSH Quick Start

Use this guide to access the team's Pi from Windows or Mac. Tailscale and SSH are already configured on the Pi.

## 1. Connect your computer to Rice Visitor

- **Windows:** Click the network icon in the taskbar → select **Rice Visitor** → **Connect**.
- **Mac:** Open the Wi-Fi menu → select **Rice Visitor**.

Complete any browser sign-in or acceptance page that appears. Confirm you can open a website.

## 2. Install Tailscale on your computer (first time only)

| Windows | Mac |
| --- | --- |
| Download and install [Tailscale for Windows](https://tailscale.com/download/windows). Open it from the Start menu or system tray. | Download and install [Tailscale for Mac](https://tailscale.com/download/mac). Open it and approve the VPN configuration when prompted. |

1. In Tailscale, select **Log in** and choose **Google**.
2. Sign in using the **CLAW team Gmail account**, not your personal account. Ask a team member for the account credentials if needed.
3. Complete the prompts to register your computer. Keep Tailscale connected.

## 3. Power on the Pi and find its IP

1. Connect the Pi 5 to its USB-C power supply.
2. Wait approximately **30 seconds** for it to boot; allow longer if it still appears offline.
3. Open the [Tailscale Machines page](https://login.tailscale.com/admin/machines), signed in with the CLAW Gmail account.
4. Find **wristpower** and copy its **Tailscale IPv4 address** (`100.x.x.x`). Check that it is online.

The Pi must have internet access through a saved Wi-Fi network. Your computer and Pi can use different networks when both are connected to Tailscale.

## 4. SSH into the Pi

- **Windows:** Open **PowerShell** or **Windows Terminal** from the Start menu.
- **Mac:** Press **Command + Space**, search for **Terminal**, and open it.

Run the following, replacing `100.x.x.x` with the address you copied:

```bash
ssh wristpower@100.x.x.x
```

- On your first connection, confirm you used the team's Pi address, then type `yes` if asked to trust the host.
- When prompted for the Pi password, enter **`wristpower`** and press Enter. Nothing appears while typing the password; this is normal.
- You are connected when the Pi's command prompt appears. Commands you enter now run on the Pi.

To disconnect:

```bash
exit
```

## Quick troubleshooting

- **Timeout / Pi offline:** Check Pi power, wait another 30–60 seconds, and confirm the Pi has Wi-Fi internet access. Verify Tailscale is connected on your computer under the CLAW account.
- **Permission denied:** Use the username `wristpower` and password `wristpower` (both lowercase).
- **Could not resolve hostname:** Use the numeric Tailscale IP from the Machines page instead of a `.local` hostname.
- **Windows says `ssh` is not recognized:** Install **OpenSSH Client** through Windows **Optional features**, then reopen the terminal. See [Microsoft's instructions](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).

For later sessions, connect your computer to the internet, keep Tailscale connected, power on the Pi, and reuse the SSH command. The Tailscale IP normally stays the same across reboots and Wi-Fi changes.

References: [Tailscale Windows setup](https://tailscale.com/docs/install/windows) · [Tailscale Mac setup](https://tailscale.com/docs/install/mac) · [Raspberry Pi remote access](https://www.raspberrypi.com/documentation/computers/remote-access.html)
