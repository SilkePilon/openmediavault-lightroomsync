# This file is part of openmediavault-lightroomsync.
#
# @license   https://www.gnu.org/licenses/gpl.html GPL Version 3

# Make sure custom Jinja filters are registered.
{% set _ = salt['omv_utils.register_jinja_filters']() %}

include:
  - .{{ salt['pillar.get']('deploy_lightroomsync', 'default') }}
