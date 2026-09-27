<?php
/**
 * This file is part of openmediavault-lightroomsync.
 *
 * @license   https://www.gnu.org/licenses/gpl.html GPL Version 3
 */
require_once("openmediavault/functions.inc");

\OMV\System\LogFileSpec::registerSpecification("lightroomsync", [
  "command" => "export SYSTEMD_COLORS=0; journalctl --quiet --no-pager ".
    "--identifier='omv-lightroomsync' --output=short-iso",
  "filename" => "lightroomsync",
  "regex" => "/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:?\d{2})\s+(\S+)\s+(.+)$/",
  "columns" => [
      "ts" => [
          "index" => 1,
          "func" => function ($v) {
              return strpdate($v, "Y-m-d*H:i:sT");
          }
      ],
      "date" => [
          "index" => 1,
          "func" => function ($v) {
              return strftime("%c", strpdate($v, "Y-m-d*H:i:sT"));
          }
      ],
      "hostname" => 2,
      "message" => 3
  ]]);
