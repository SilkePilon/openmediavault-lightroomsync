# This file is part of openmediavault-lightroomsync.
#
# @license   https://www.gnu.org/licenses/gpl.html GPL Version 3

{% set dirpath = '/srv/salt' | path_join(tpldir) %}

include:
{% for file in salt['file.readdir'](dirpath) | sort %}
{% if file | regex_match('^(\d+.+).sls$', ignorecase=True) %}
  - .{{ file | replace('.sls', '') }}
{% endif %}
{% endfor %}
