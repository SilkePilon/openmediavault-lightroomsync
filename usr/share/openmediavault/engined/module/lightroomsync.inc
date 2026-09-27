<?php
/**
 * This file is part of openmediavault-lightroomsync.
 *
 * @license   https://www.gnu.org/licenses/gpl.html GPL Version 3
 */
namespace Engined\Module;

class LightroomSync extends \OMV\Engine\Module\ServiceAbstract
    implements \OMV\Engine\Notify\IListener, \OMV\Engine\Module\IServiceStatus
{
    public function getName()
    {
        // Must match the Salt state directory srv/salt/omv/deploy/lightroomsync.
        return "lightroomsync";
    }

    public function getStatus()
    {
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get("conf.service.lightroomsync");
        $systemCtl = new \OMV\System\SystemCtl("omv-lightroomsync.timer");
        return [
            "name" => $this->getName(),
            "title" => gettext("Lightroom Sync"),
            "enabled" => $object->get("enable"),
            "running" => $systemCtl->isActive()
        ];
    }

    /**
     * The runtime configuration contains the resolved shared folder path,
     * so it must be redeployed when that shared folder changes.
     */
    final public function onModifySharedFolder($type, $path, $object)
    {
        $db = \OMV\Config\Database::getInstance();
        if (true === $db->exists("conf.service.lightroomsync", [
            "operator" => "stringEquals",
            "arg0" => "sharedfolderref",
            "arg1" => $object['uuid']
        ])) {
            $this->setDirty();
        }
    }

    public function bindListeners(\OMV\Engine\Notify\Dispatcher $dispatcher)
    {
        $dispatcher->addListener(
            OMV_NOTIFY_MODIFY,
            "org.openmediavault.conf.service.lightroomsync",
            [$this, "setDirty"]
        );
        $dispatcher->addListener(
            OMV_NOTIFY_MODIFY,
            "org.openmediavault.conf.system.sharedfolder",
            [$this, "onModifySharedFolder"]
        );
    }
}
