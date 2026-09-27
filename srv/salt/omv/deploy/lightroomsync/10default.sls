# This file is part of openmediavault-lightroomsync.
#
# @license   https://www.gnu.org/licenses/gpl.html GPL Version 3

# Renders the runtime configuration read by omv-lightroomsync and the
# systemd service/timer that run it on schedule.
#
# Testing:
# omv-salt deploy run lightroomsync
# cat /etc/openmediavault-lightroomsync/config.json
# systemctl list-timers omv-lightroomsync.timer

{% set config = salt['omv_conf.get']('conf.service.lightroomsync') %}
{% set state_dir = '/var/lib/openmediavault-lightroomsync' %}
{% set calendars = {
  'hourly': 'hourly',
  '6h': '*-*-* 00/6:00:00',
  'daily': '*-*-* 03:00:00',
  'weekly': 'Sun *-*-* 03:00:00'
} %}

{% if config.enable | to_bool and config.sharedfolderref | length > 0 %}

{% set sf_path = salt['omv_conf.get_sharedfolder_path'](config.sharedfolderref) %}
{% set runtime = {
  'base_dir': sf_path,
  'target_dir': sf_path | path_join(config.subdir),
  'owner': config.username,
  'group': 'users',
  'source': config.source,
  'albums': config.albums.split(',') | reject('equalto', '') | list,
  'hide_stacked': config.hidestacked | to_bool,
  'captured_after': config.capturedafter,
  'folder_template': config.foldertemplate,
  'filename_template': config.filenametemplate,
  'rendition': config.rendition,
  'rendition_fallback': config.renditionfallback | to_bool,
  'max_dimension': config.maxdimension | int,
  'jpeg_quality': config.jpegquality | int,
  'keep_metadata': config.keepmetadata | to_bool,
  'set_mtime': config.setmtime | to_bool,
  'redownload_edited': config.redownloadedited | to_bool,
  'delete_mode': config.deletemode,
  'concurrency': config.concurrency | int
} %}

create_lightroomsync_config:
  file.managed:
    - name: /etc/openmediavault-lightroomsync/config.json
    - contents: |
        {{ runtime | json }}
    - makedirs: True
    - user: root
    - group: root
    - mode: '0644'

create_lightroomsync_service:
  file.managed:
    - name: /etc/systemd/system/omv-lightroomsync.service
    - contents: |
        {{ pillar['headers']['multiline'] | indent(8) }}
        [Unit]
        Description=Lightroom Sync for openmediavault
        Wants=network-online.target
        After=network-online.target
        RequiresMountsFor="{{ sf_path }}"

        [Service]
        Type=oneshot
        ExecStart=/usr/sbin/omv-lightroomsync sync
        SyslogIdentifier=omv-lightroomsync
        Nice=10
        IOSchedulingClass=idle
        StateDirectory=openmediavault-lightroomsync
        StateDirectoryMode=0700
        PrivateTmp=yes
        NoNewPrivileges=yes
        ProtectSystem=strict
        ReadWritePaths="{{ sf_path }}" {{ state_dir }}
    - mode: '0644'

{% if config.schedule in calendars %}

create_lightroomsync_timer:
  file.managed:
    - name: /etc/systemd/system/omv-lightroomsync.timer
    - contents: |
        {{ pillar['headers']['multiline'] | indent(8) }}
        [Unit]
        Description=Scheduled Lightroom Sync

        [Timer]
        OnCalendar={{ calendars[config.schedule] }}
        RandomizedDelaySec={{ '5min' if config.schedule == 'hourly' else '15min' }}
        Persistent=true

        [Install]
        WantedBy=timers.target
    - mode: '0644'

lightroomsync_systemctl_daemon_reload:
  module.run:
    - service.systemctl_reload:
    - onchanges:
      - file: create_lightroomsync_service
      - file: create_lightroomsync_timer

start_lightroomsync_timer:
  service.running:
    - name: omv-lightroomsync.timer
    - enable: True
    - watch:
      - file: create_lightroomsync_timer

{% else %}

stop_lightroomsync_timer:
  service.dead:
    - name: omv-lightroomsync.timer
    - enable: False

remove_lightroomsync_timer:
  file.absent:
    - name: /etc/systemd/system/omv-lightroomsync.timer

lightroomsync_systemctl_daemon_reload:
  module.run:
    - service.systemctl_reload:
    - onchanges:
      - file: create_lightroomsync_service
      - file: remove_lightroomsync_timer

{% endif %}

{% else %}

stop_lightroomsync_timer:
  service.dead:
    - name: omv-lightroomsync.timer
    - enable: False

remove_lightroomsync_units:
  file.absent:
    - names:
      - /etc/systemd/system/omv-lightroomsync.timer
      - /etc/systemd/system/omv-lightroomsync.service

remove_lightroomsync_config:
  file.absent:
    - name: /etc/openmediavault-lightroomsync/config.json

lightroomsync_systemctl_daemon_reload:
  module.run:
    - service.systemctl_reload:
    - onchanges:
      - file: remove_lightroomsync_units

{% endif %}
