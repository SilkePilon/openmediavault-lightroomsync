# openmediavault-lightroomsync

Back up your Adobe Lightroom photos to your NAS.

This is a plugin for [openmediavault](https://www.openmediavault.org). It
downloads the photos from your Lightroom cloud library (or from the albums
you pick) into a shared folder, sorted into folders and named the way you
want. It runs on a schedule and only fetches what's new or changed.

> **Status:** early release. The sync engine has a full test suite, but the
> plugin hasn't been tested on many real systems yet. Please
> [open an issue](https://github.com/SilkePilon/openmediavault-lightroomsync/issues)
> if something doesn't work.

## Features

- Sync your whole library or only selected albums. Nested albums become
  nested folders.
- Choose your own folder layout and file names with templates like
  `{year}/{year}-{month}` and `{year}{month}{day}_{name}`.
- Pick the resolution: full size, 2560, 2048, 1280 or 640 pixels on the long
  edge.
- Optionally shrink photos on the NAS to save disk space, and strip location
  and camera data if you prefer.
- Edited photos are downloaded again, so your backup matches what you see in
  Lightroom.
- Decide what happens when you delete a photo in Lightroom: keep your copy,
  move it to a trash folder, or delete it.
- Run it hourly, every 6 hours, daily, weekly, or only when you press
  *Sync now*.
- Preview a sync with *Dry run* before anything is written.

## Before you start: what Adobe allows

The plugin uses Adobe's official Lightroom API, which has some limits you
should know about:

- **You get JPEG files, not your originals.** The API doesn't offer RAW,
  DNG or HEIC originals. You get a JPEG of each photo with your edits applied.
- **Full size and 2560 px need extra access from Adobe.** 2048 px and smaller
  always work. If your Adobe key isn't approved for full size, the plugin
  saves the largest size it can get and tells you so in the log.
- **Videos aren't downloaded.** The API doesn't provide them.
- **You'll have to sign in again every two weeks or so.** Adobe's sign-in
  expires after about 14 days. The Status page shows the expiry date.
- **You need a free Adobe developer key.** The plugin doesn't come with its
  own. Setting one up takes about five minutes and is described below.

You also need an active Lightroom plan. Any plan that includes cloud storage
works.

## Requirements

- openmediavault 7 or 8
- An Adobe account with a Lightroom plan

## Installation

There's no package repository yet, so you build the package yourself. On the
NAS (or any Debian machine):

```sh
sudo apt install git debhelper python3-pil
git clone https://github.com/SilkePilon/openmediavault-lightroomsync.git
cd openmediavault-lightroomsync
dpkg-buildpackage -us -uc -b
sudo apt install ../openmediavault-lightroomsync_*_all.deb
```

Reload the openmediavault web interface. You'll find the plugin under
**Services > Lightroom Sync**.

## Setup

### 1. Create an Adobe developer key

1. Go to the [Adobe Developer Console](https://developer.adobe.com/console)
   and sign in with the same Adobe account you use for Lightroom.
2. Create a new project, then click **Add API** and choose
   **Lightroom Services**.
3. When asked for a credential type, choose **OAuth Web App**.
4. Fill in the redirect settings:
   - Default redirect URI: `https://localhost/`
   - Redirect URI pattern: `https://localhost/.*`
5. Open the credential's **Beta users** section and add your own Adobe
   e-mail address. Until Adobe reviews a key, only the people listed there
   can use it.
6. Keep the page open. You'll need the **Client ID** and **Client secret**.

### 2. Connect the plugin to your Adobe account

1. In openmediavault, open **Services > Lightroom Sync > Adobe Account**.
2. Paste the client ID and client secret, then click **Open Adobe sign-in**.
3. Sign in in the new tab. Afterwards the browser tries to open
   `https://localhost/?code=...` and shows an error page. That's expected.
4. Copy the full address from the address bar, paste it into
   **Redirected URL**, and click **Complete sign-in**.

Why the error page? Adobe only sends you back to an `https://` address, and
most home NAS setups can't serve one. Copying the address over by hand works
around that.

### 3. Choose what to sync

1. Open **Settings**, tick **Enabled** and pick a shared folder.
2. Adjust the options (see below) and click **Save**.
3. Click **Apply** in the yellow bar at the top.
4. Go to **Status** and click **Dry run** to see what would be downloaded.
   If it looks right, click **Sync now**.

The first sync of a large library can take a few hours. Later syncs only
fetch what changed.

## Settings

| Setting | What it does |
|---|---|
| Schedule | How often the sync runs automatically. *Manually only* disables the timer. |
| Shared folder / Subfolder | Where the photos go. The default subfolder is `Lightroom`. |
| File owner | The user who owns the downloaded files. The group is always `users`. |
| Source | Whole library or selected albums. Albums show up after you've signed in. |
| Only photos taken on or after | Skip older photos, e.g. `2020-01-01`. |
| Skip stacked photos | Only download the top photo of each stack. |
| Folder template / File name template | See [Naming](#naming). |
| Set file date to capture date | Makes photo apps and file browsers sort by when the photo was taken. |
| Resolution | The size to download. See the note on full size above. |
| Fall back to smaller sizes | If the chosen size isn't available, use the next smaller one instead of skipping the photo. |
| Limit long edge | Shrink photos on the NAS. `0` keeps them as downloaded. |
| JPEG quality | Only used when a photo is shrunk or its metadata is removed. Otherwise the file is saved exactly as Adobe sends it. |
| Keep metadata | Keep EXIF data such as camera settings and GPS location. |
| Update edited photos | Download a photo again after you edit it in Lightroom. |
| When a photo is removed in Lightroom | Keep the local file, move it to `.lightroom-trash`, or delete it. This also applies to photos removed from a selected album. |
| Parallel downloads | How many photos to download at once. 4 is a good default. |

As a safety net, a sync refuses to remove more than half of your synced files
in one go. That protects your backup if Lightroom ever returns an empty
library by mistake. If you really do want to remove that many, run
`omv-lightroomsync sync --allow-mass-delete` once.

## Naming

Folder and file names are built from templates. You can use these tokens:

| Token | Example | Meaning |
|---|---|---|
| `{year}` | `2024` | Year the photo was taken |
| `{month}` | `03` | Month, two digits |
| `{monthname}` | `March` | Month name |
| `{day}` | `05` | Day, two digits |
| `{hour}` `{minute}` `{second}` | `14` `15` `16` | Time the photo was taken |
| `{name}` | `IMG_0412` | Original file name, without extension |
| `{id}` / `{id8}` | `a1b2c3d4` | Lightroom's ID for the photo (full / first 8 characters) |
| `{album}` | `Summer` | Album name (album sync only) |
| `{albumpath}` | `Trips/2024/Summer` | Album name including parent folders (album sync only) |

Use `/` in the folder template to create subfolders. `.jpg` is added to the
file name automatically.

Some examples:

| Folder template | File name template | Result |
|---|---|---|
| `{year}/{year}-{month}` | `{year}{month}{day}_{hour}{minute}{second}_{name}` | `2024/2024-03/20240305_141516_IMG_0412.jpg` (default) |
| `{albumpath}` | `{name}` | `Trips/2024/Summer/IMG_0412.jpg` |
| `{year}/{monthname}` | `{name}` | `2024/March/IMG_0412.jpg` |
| *(empty)* | `{year}-{month}-{day} {name}` | `2024-03-05 IMG_0412.jpg` |

If two photos end up with the same name, the second one gets a suffix such as
`_2`. The plugin never overwrites files it didn't create. If you change a
template later, existing files are renamed on the next sync instead of being
downloaded again.

## Command line

Everything in the web interface is also available from a shell on the NAS:

```sh
omv-lightroomsync sync             # sync now
omv-lightroomsync sync --dry-run   # show what would happen
omv-lightroomsync status           # sign-in state and last sync
omv-lightroomsync albums           # list your albums and their IDs
omv-lightroomsync tokens           # list the naming tokens
omv-lightroomsync auth-logout      # remove the stored Adobe sign-in
```

Logs from scheduled runs are under **Diagnostics > System Logs > Lightroom
Sync**, or you can run `journalctl -t omv-lightroomsync`.

## Troubleshooting

**The album list is empty.** Sign in on the Adobe Account page first, then
reload the Settings page.

**"Not signed in" or sign-in errors after a couple of weeks.** Adobe's
sign-in has expired. Repeat step 2 of the setup.

**Everything downloads at 2048 px even though I chose full size.** Your Adobe
key doesn't have access to full-size images. The log shows a warning when
this happens. You can ask Adobe for access through the Developer Console, or
just use 2048 px.

**"Refusing to remove ... synced files".** See the safety net described under
[Settings](#settings).

**Starting over.** Run `omv-lightroomsync sync --reset-state` to make the
plugin forget what it has synced. Existing files are kept, and new downloads
that would clash with them get a suffix.

## How it works

The plugin has two parts:

- **The sync engine**, a small Python program (`omv-lightroomsync`). It only
  needs the Python standard library plus Pillow, which it uses for resizing.
- **The openmediavault integration.** This covers the web pages, the RPC
  service, and a Salt state. When you click Apply, the Salt state writes the
  engine's config to `/etc/openmediavault-lightroomsync/config.json` and
  sets up a systemd timer.

Each run lists your library, compares it with a small SQLite database of what
was synced before, and downloads only new or edited photos. Listing
everything on every run is also how the plugin notices deleted photos,
because the Lightroom API doesn't report deletions.

Your Adobe sign-in, the sync database and the last-run status are stored in
`/var/lib/openmediavault-lightroomsync/`, which only root can read.
Uninstalling with `apt purge` removes that folder and the plugin's settings.
Your downloaded photos are never touched.

## Development

The engine can be developed and tested without openmediavault:

```sh
python3 -m venv .venv
.venv/bin/pip install pillow
cd tests && ../.venv/bin/python -m unittest -v
```

The tests run against an in-memory fake of Adobe's API
(`tests/fake_lightroom.py`), so no Adobe account is needed.

Project layout:

```
usr/lib/openmediavault-lightroomsync/   sync engine (Python)
usr/sbin/omv-lightroomsync              command line entry point
usr/share/openmediavault/engined/       RPC service and module (PHP)
usr/share/openmediavault/datamodels/    settings schema
usr/share/openmediavault/workbench/     web interface pages (YAML)
srv/salt/omv/deploy/lightroomsync/      config and systemd timer deployment
debian/                                 packaging
tests/                                  engine tests
```

## Contributing

Bug reports, testing on your own NAS, and pull requests are all welcome. If
you're changing the sync logic, please add a test in `tests/test_sync.py`.

## License

GPL-3.0-or-later, the same as openmediavault. See [LICENSE](LICENSE).

This project isn't affiliated with or endorsed by Adobe. Adobe and Lightroom
are trademarks of Adobe Inc.
