#!/usr/bin/env dash
#
# This file is part of openmediavault-lightroomsync.
#
# @license   https://www.gnu.org/licenses/gpl.html GPL Version 3

set -e

. /usr/share/openmediavault/scripts/helper-functions

########################################################################
# Update the configuration.
# <config>
#   <services>
#     <lightroomsync>
#       <enable>0|1</enable>
#       <sharedfolderref></sharedfolderref>
#       <subdir>Lightroom</subdir>
#       <username></username>
#       <clientid></clientid>
#       <clientsecret></clientsecret>
#       <redirecturi>https://localhost/</redirecturi>
#       <source>all|albums</source>
#       <albums>id1,id2</albums>
#       <hidestacked>0|1</hidestacked>
#       <capturedafter>YYYY-MM-DD</capturedafter>
#       <foldertemplate>{year}/{year}-{month}</foldertemplate>
#       <filenametemplate>...</filenametemplate>
#       <rendition>fullsize|2560|2048|1280|640</rendition>
#       <renditionfallback>0|1</renditionfallback>
#       <maxdimension>0</maxdimension>
#       <jpegquality>92</jpegquality>
#       <keepmetadata>0|1</keepmetadata>
#       <setmtime>0|1</setmtime>
#       <redownloadedited>0|1</redownloadedited>
#       <deletemode>keep|trash|delete</deletemode>
#       <schedule>manual|hourly|6h|daily|weekly</schedule>
#       <concurrency>4</concurrency>
#     </lightroomsync>
#   </services>
# </config>
########################################################################
if ! omv_config_exists "/config/services/lightroomsync"; then
	omv_config_add_node "/config/services" "lightroomsync"
	omv_config_add_key "/config/services/lightroomsync" "enable" "0"
	omv_config_add_key "/config/services/lightroomsync" "sharedfolderref" ""
	omv_config_add_key "/config/services/lightroomsync" "subdir" "Lightroom"
	omv_config_add_key "/config/services/lightroomsync" "username" ""
	omv_config_add_key "/config/services/lightroomsync" "clientid" ""
	omv_config_add_key "/config/services/lightroomsync" "clientsecret" ""
	omv_config_add_key "/config/services/lightroomsync" "redirecturi" "https://localhost/"
	omv_config_add_key "/config/services/lightroomsync" "source" "all"
	omv_config_add_key "/config/services/lightroomsync" "albums" ""
	omv_config_add_key "/config/services/lightroomsync" "hidestacked" "0"
	omv_config_add_key "/config/services/lightroomsync" "capturedafter" ""
	omv_config_add_key "/config/services/lightroomsync" "foldertemplate" "{year}/{year}-{month}"
	omv_config_add_key "/config/services/lightroomsync" "filenametemplate" "{year}{month}{day}_{hour}{minute}{second}_{name}"
	omv_config_add_key "/config/services/lightroomsync" "rendition" "2048"
	omv_config_add_key "/config/services/lightroomsync" "renditionfallback" "1"
	omv_config_add_key "/config/services/lightroomsync" "maxdimension" "0"
	omv_config_add_key "/config/services/lightroomsync" "jpegquality" "92"
	omv_config_add_key "/config/services/lightroomsync" "keepmetadata" "1"
	omv_config_add_key "/config/services/lightroomsync" "setmtime" "1"
	omv_config_add_key "/config/services/lightroomsync" "redownloadedited" "1"
	omv_config_add_key "/config/services/lightroomsync" "deletemode" "keep"
	omv_config_add_key "/config/services/lightroomsync" "schedule" "daily"
	omv_config_add_key "/config/services/lightroomsync" "concurrency" "4"
fi

exit 0
