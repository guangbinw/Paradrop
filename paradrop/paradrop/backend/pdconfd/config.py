import ipaddress
import os
import subprocess
from pprint import pprint

from paradrop.internal.utils.uci import OpenWrtConfig

CONFIG_DIR = "/etc/config"

class ConfigObject(object):
    nextId = 0
    typename = None
    options = []

    def __init__(self):
        self.id = ConfigObject.nextId
        ConfigObject.nextId += 1

        self.name = None

        self.dependents = set()

    def __hash__(self):
        return hash(self.getTypeAndName())

    def addDependent(self, dep):
        self.dependents.add(dep)

    def commands(self, allConfigs):
        return []

    def getTypeAndName(self):
        """
        Return tuple (section type, section name).
        """
        return (self.typename, self.name)

    def undoCommands(self, allConfigs):
        return []

    def optionsMatch(self, other):
        """
        Test equality of config sections by comparing option values.
        """
        if not isinstance(other, self.__class__):
            return False
        for opdef in self.options:
            if getattr(self, opdef['name']) != getattr(other, opdef['name']):
                return False
        return True

    @classmethod
    def build(cls, source, name, options):
        """
        Build a config object instance from the UCI section.

        Arguments:
        source -- file containing this configuration section
        name -- name of the configuration section
        options -- dictionary of options loaded from the section
        """
        obj = cls()
        obj.source = source
        obj.name = name

        for opdef in cls.options:
            found = False

            if opdef['type'] == list:
                if "list" in options and opdef['name'] in options['list']:
                    value = options['list'][opdef['name']]
                    found = True
            elif opdef['type'] == bool:
                if opdef['name'] in options:
                    value = options[opdef['name']] != '0'
                    found = True
            else:
                if opdef['name'] in options:
                    value = opdef['type'](options[opdef['name']])
                    found = True

            if not found:
                if opdef['required']:
                    raise Exception("Missing required option {} in {}:{}:{}".format(
                        opdef['name'], source, cls.typename, name))
                else:
                    value = opdef['default']

            setattr(obj, opdef['name'], value)

        return obj

class ConfigDhcp(ConfigObject):
    typename = "dhcp"

    options = [
        {"name": "interface", "type": str, "required": True, "default": None},
        {"name": "leasetime", "type": str, "required": True, "default": "12h"},
        {"name": "limit", "type": int, "required": True, "default": 150},
        {"name": "start", "type": int, "required": True, "default": 100}
    ]

    def commands(self, allConfigs):
        # Look up the interface - may fail.
        interface = allConfigs[("interface", self.interface)]
        interface.addDependent(self)

        network = ipaddress.IPv4Network(u"{}/{}".format(
            interface.ipaddr, interface.netmask), strict=False)

        # TODO: Error checking!
        firstAddress = network.network_address + self.start
        lastAddress = firstAddress + self.limit

        leaseFile = "/tmp/dnsmasq-{}.leases".format(self.interface)
        pidFile = "/tmp/dnsmasq-{}.pid".format(self.interface)

        if False:
            # TODO: Figure out how to write a configuration file and have the
            # target daemon be able to load that file.  In principle this code
            # should work, but something about how Snappy handles filesystem or
            # process isolation breaks it.  We write the file
            # /etc/dnsmasq-foo.conf, it appears in the filesystem, but then
            # dnsmasq says it cannot find it.  Luckily, dnsmasq can be
            # configured either by command line arguments or by configuration
            # file.  Other daemons (e.g.  hostapd) are not so flexible, so we
            # need to solve this.
            outputPath = "/tmp/dnsmasq-{}.conf".format(self.interface)
            with open(outputPath, "w") as outputFile:
                outputFile.write("#" * 80 + "\n")
                outputFile.write("# dnsmasq configuration file generated by pdconfd\n")
                outputFile.write("# Source: {}\n".format(self.source))
                outputFile.write("# Section: config {} {}\n".format(
                    ConfigDhcp.typename, self.name))
                outputFile.write("#" * 80 + "\n")
                outputFile.write("interface={}\n".format(interface.ifname))
                outputFile.write("dhcp-range={},{},{}\n".format(
                    str(firstAddress), str(lastAddress), self.leasetime))
                outputFile.write("dhcp-leasefile={}\n".format(leaseFile))
            cmd = ["dnsmasq", "-C", outputPath]
        else:
            cmd = ["/apps/paradrop/current/bin/dnsmasq",
                    "--interface={}".format(interface.ifname),
                    "--dhcp-range={},{},{}".format(str(firstAddress), str(lastAddress), self.leasetime),
                    "--dhcp-leasefile={}".format(leaseFile),
                    "--pid-file={}".format(pidFile)]

        self.pidFile = pidFile
        return [cmd]

    def undoCommands(self, allConfigs):
        with open(self.pidFile, "r") as inputFile:
            pid = inputFile.read().strip()
        cmd = ["kill", pid]
        return [cmd]

class ConfigInterface(ConfigObject):
    typename = "interface"

    options = [
        {"name": "proto", "type": str, "required": True, "default": None},
        {"name": "ifname", "type": str, "required": True, "default": None},
        {"name": "enabled", "type": bool, "required": False, "default": True},
        {"name": "ipaddr", "type": str, "required": False, "default": None},
        {"name": "netmask", "type": str, "required": False, "default": None}
    ]

    def commands(self, allConfigs):
        commands = list()
        if self.proto == "static":
            cmd = ["ip", "addr", "flush", "dev", self.ifname]
            commands.append(cmd)

            cmd = ["ip", "addr", "add", 
                    "{}/{}".format(self.ipaddr, self.netmask),
                    "dev", self.ifname]
            commands.append(cmd)

            updown = "up" if self.enabled else "down"
            cmd = ["ip", "link", "set", "dev", self.ifname, updown]
            commands.append(cmd)

        return commands

class ConfigZone(ConfigObject):
    typename = "zone"

    options = [
        {"name": "name", "type": str, "required": True, "default": None},
        {"name": "network", "type": list, "required": False, "default": None},
        {"name": "masq", "type": bool, "required": False, "default": False},
        {"name": "input", "type": str, "required": False, "default": "DROP"},
        {"name": "forward", "type": str, "required": False, "default": "DROP"},
        {"name": "output", "type": str, "required": False, "default": "DROP"}
    ]

    def __commands(self, allConfigs, action):
        commands = list()

        if self.network is not None:
            for networkName in self.network:
                # Look up the interface - may fail.
                interface = allConfigs[("interface", networkName)]
                interface.addDependent(self)

                cmd = ["iptables", "--table", "nat",
                        action, "POSTROUTING",
                        "--out-interface", interface.ifname,
                        "--jump", "MASQUERADE"]
                commands.append(cmd) 

        return commands

    def commands(self, allConfigs):
        return self.__commands(allConfigs, "--insert")

    def undoCommands(self, allConfigs):
        return self.__commands(allConfigs, "--delete")

# Map of type names to the classes that handle them.
configTypeMap = dict()
for cls in ConfigObject.__subclasses__():
    configTypeMap[cls.typename] = cls

def findConfigFiles(search=None):
    """
    Look for and return a list of configuration files.  
    
    The behavior depends on whether the search argument is a file, a directory,
    or None.

    If search is None, return a list of files in the system config directory.
    If search is a file name (not a path), look for it in the working directory
    first, and the system directory second.  If search is a full path to a
    file, and it exists, then return that file.  If search is a directory,
    return the files in that directory.
    """
    if search is None:
        search = CONFIG_DIR

    files = list()
    if os.path.isfile(search):
        files.append(search)
    elif os.path.isdir(search):
        for fn in os.listdir(search):
            path = "{}/{}".format(search, fn)
            files.append(path)
    else:
        path = "{}/{}".format(CONFIG_DIR, search)
        if os.path.isfile(path):
            files.append(path)

    return files

class ConfigManager(object):
    def __init__(self):
        self.currentConfig = dict()
        self.nextSectionId = 0

    def loadConfig(self, search=None, execute=True):
        files = findConfigFiles(search)

        # Map (type, name) -> config
        allConfigs = dict(self.currentConfig)

        # Manage sets of configuration sections.
        # newConfigs: completely new or new versions of existing sections.
        # affectedConfigs: sections that are affected due to dependency changing.
        # undoConfigs: old sections that need to be undone before proceeding.
        newConfigs = set()
        affectedConfigs = set()
        undoConfigs = set()

        # Final list of commands to execute.
        commands = list()

        # First, parse all of the new configuration files.  There may be
        # dependencies across files, so we read them all in first.
        for fn in files:
            print("Trying file {}".format(fn))
            uci = OpenWrtConfig(fn)
            config = uci.readConfig()

            if config is None:
                print("Error reading file.")
                continue

            for section, options in config:
                if "name" in section:
                    name = section['name']
                elif "name" in options:
                    name = options['name']
                else:
                    name = "section{:04d}".format(self.nextSectionId)
                    self.nextSectionId += 1

                cls = configTypeMap[section['type']]
                obj = cls.build(fn, name, options)
                key = obj.getTypeAndName()

                # Check if the section already exists in identical form
                # in our current configuration.
                matches = False
                if key in self.currentConfig:
                    oldobj = self.currentConfig[key]
                    if obj.optionsMatch(oldobj):
                        matches = True
                    else:
                        # Old section will need to be undone appropriately.
                        undoConfigs.add(oldobj)

                        # Keep track of sections that may be affected by this
                        # one's change.
                        affectedConfigs.update(oldobj.dependents)

                # If it did not exist or is different, add it to our queue
                # of sections to execute.
                if not matches:
                    newConfigs.add(obj)
                    allConfigs[(cls.typename, name)] = obj

        # Generate list of commands to implement configuration.
        for config in affectedConfigs:
            commands.extend(config.undoCommands(self.currentConfig))
        for config in undoConfigs:
            commands.extend(config.undoCommands(self.currentConfig))
        for config in newConfigs:
            commands.extend(config.commands(allConfigs))
        for config in affectedConfigs:
            commands.extend(config.commands(allConfigs))

        # Finally, execute the commands.
        for cmd in commands:
            print("Command: {}".format(" ".join(cmd)))
            if execute:
                result = subprocess.call(cmd)
                print("Result: {}".format(result))

        self.currentConfig = allConfigs
        return True

if __name__=="__main__":
    manager = ConfigManager()
    manager.loadConfig(execute=False)

