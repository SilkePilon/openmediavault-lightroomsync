<?php
/**
 * This file is part of openmediavault-lightroomsync.
 *
 * @license   https://www.gnu.org/licenses/gpl.html GPL Version 3
 *
 * openmediavault-lightroomsync is free software: you can redistribute it
 * and/or modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or any later version.
 */
namespace Engined\Rpc;

class LightroomSync extends \OMV\Rpc\ServiceAbstract
{
    const CLI = "omv-lightroomsync";
    // Account properties are only managed on the Account page.
    const ACCOUNT_PROPS = ["clientid", "clientsecret", "redirecturi"];

    public function getName()
    {
        return "LightroomSync";
    }

    public function initialize()
    {
        $this->registerMethod("getSettings");
        $this->registerMethod("setSettings");
        $this->registerMethod("getAccount");
        $this->registerMethod("setAccount");
        $this->registerMethod("getAuthUrl");
        $this->registerMethod("completeAuth");
        $this->registerMethod("logout");
        $this->registerMethod("getAlbums");
        $this->registerMethod("getStatus");
        $this->registerMethod("sync");
    }

    /**
     * Run the CLI and return its output lines. Arguments are shell-escaped;
     * secrets must be passed via $stdinData, never as arguments, because
     * the command line ends up in logs and error messages.
     */
    private function runCli(array $args, $stdinData = null)
    {
        $cmd = new \OMV\System\Process(self::CLI,
            array_map("escapeshellarg", $args));
        $cmd->setRedirect2to1();
        $cmd->setQuiet(true);
        $tmpFile = null;
        if (!is_null($stdinData)) {
            $tmpFile = tempnam(sys_get_temp_dir(), "lrsync");
            chmod($tmpFile, 0600);
            file_put_contents($tmpFile, $stdinData);
            $cmd->setInputFromFile($tmpFile);
        }
        try {
            $cmd->execute($output, $exitStatus);
        } finally {
            if (!is_null($tmpFile)) {
                unlink($tmpFile);
            }
        }
        if (0 !== $exitStatus) {
            throw new \OMV\Exception(implode("\n", $output));
        }
        return $output;
    }

    private function getObject()
    {
        $db = \OMV\Config\Database::getInstance();
        return $db->get("conf.service.lightroomsync");
    }

    /**
     * Update only the given properties, so the Settings and Account pages
     * do not overwrite each other's values.
     */
    private function mergeObject(array $data)
    {
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get("conf.service.lightroomsync");
        $object->setAssoc($data, true, true);
        $db->set($object);
        return $object;
    }

    public function getSettings($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $result = $this->getObject()->getAssoc();
        foreach (self::ACCOUNT_PROPS as $prop) {
            unset($result[$prop]);
        }
        $result['albums'] = array_values(array_filter(
            explode(",", $result['albums'])));
        return $result;
    }

    public function setSettings($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        foreach (self::ACCOUNT_PROPS as $prop) {
            unset($params[$prop]);
        }
        if (array_key_exists("albums", $params) && is_array($params['albums'])) {
            $params['albums'] = implode(",", $params['albums']);
        }
        if ($params['enable'] ?? false) {
            if (empty($params['sharedfolderref'])) {
                throw new \OMV\Exception(gettext(
                    "A shared folder is required to enable the sync."));
            }
            if (("albums" === ($params['source'] ?? "")) &&
                    empty($params['albums'])) {
                throw new \OMV\Exception(gettext(
                    "Select at least one album or sync the whole library."));
            }
        }
        $this->mergeObject($params);
        return $this->getSettings([], $context);
    }

    public function getAccount($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $object = $this->getObject();
        $status = json_decode(implode("\n",
            $this->runCli(["status", "--json"])), true);
        if ($status['authorized'] ?? false) {
            $authStatus = sprintf(gettext("Signed in as %s"),
                $status['account'] ?: gettext("(unknown)"));
            if (!empty($status['refresh_expires_at'])) {
                $authStatus .= sprintf(gettext(", sign-in valid until about %s"),
                    date("Y-m-d", intval($status['refresh_expires_at'])));
            }
        } else {
            $authStatus = gettext("Not signed in");
        }
        return [
            "clientid" => $object->get("clientid"),
            "clientsecret" => $object->get("clientsecret"),
            "redirecturi" => $object->get("redirecturi"),
            "authstatus" => $authStatus
        ];
    }

    public function setAccount($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $this->validateMethodParams($params, "rpc.lightroomsync.setaccount");
        $this->mergeObject(array_intersect_key($params,
            array_flip(self::ACCOUNT_PROPS)));
        return $this->getAccount([], $context);
    }

    /**
     * Save the credentials currently entered in the form and return the
     * Adobe sign-in URL.
     */
    public function getAuthUrl($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $this->validateMethodParams($params, "rpc.lightroomsync.setaccount");
        if (empty($params['clientid']) || empty($params['clientsecret'])) {
            throw new \OMV\Exception(gettext(
                "Enter the client ID and client secret first."));
        }
        $this->mergeObject(array_intersect_key($params,
            array_flip(self::ACCOUNT_PROPS)));
        $output = $this->runCli(["auth-url", "--client-id", $params['clientid'],
            "--redirect-uri", $params['redirecturi']]);
        return ["url" => trim(implode("", $output))];
    }

    public function completeAuth($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $this->validateMethodParams($params, "rpc.lightroomsync.completeauth");
        $this->mergeObject(array_intersect_key($params,
            array_flip(self::ACCOUNT_PROPS)));
        $output = $this->runCli(["auth-complete"], json_encode([
            "client_id" => $params['clientid'],
            "client_secret" => $params['clientsecret'],
            "redirect_uri" => $params['redirecturi'],
            "response" => $params['authresponse']
        ]));
        return ["message" => implode("\n", $output)];
    }

    public function logout($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $this->runCli(["auth-logout"]);
    }

    public function getAlbums($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        try {
            $output = $this->runCli(["albums", "--json"]);
        } catch (\Exception $e) {
            // Do not break the Settings page if Adobe is unreachable or the
            // sign-in expired; the Status page shows the reason.
            syslog(LOG_WARNING, sprintf("Lightroom Sync: listing albums failed: %s",
                $e->getMessage()));
            return [];
        }
        return json_decode(implode("\n", $output), true) ?: [];
    }

    public function getStatus($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        return ["status" => implode("\n", $this->runCli(["status"]))];
    }

    /**
     * Run a sync as a background task that streams its output to the UI.
     */
    public function sync($params, $context)
    {
        $this->validateMethodContext($context, ["role" => OMV_ROLE_ADMINISTRATOR]);
        $this->validateMethodParams($params, "rpc.lightroomsync.sync");
        // The runtime configuration is only written when the changes are
        // applied, so an unapplied config would sync with stale settings.
        if ($this->isModuleDirty("lightroomsync")) {
            throw new \OMV\Config\ConfigDirtyException();
        }
        return $this->execBgProc(function ($bgStatusFilename, $bgOutputFilename)
                use ($params) {
            $args = ["sync"];
            if ($params['dryrun']) {
                $args[] = "--dry-run";
            }
            $cmd = new \OMV\System\Process(self::CLI, $args);
            $cmd->setRedirect2to1();
            $cmdLine = $cmd->getCommandLine();
            $exitStatus = $this->exec($cmdLine, $output, $bgOutputFilename);
            // Exit status 2 means some photos failed; their errors are
            // already part of the output, so do not raise a second error.
            if (!in_array($exitStatus, [0, 2], true)) {
                throw new \OMV\ExecException($cmdLine, $output, $exitStatus);
            }
            return $output;
        });
    }
}
