import sys
import argparse
import json
import urllib
import subprocess

LOG_FILE = "/var/snap/paradrop-daemon/common/logs/log"

def parseLine(line):
    try:
        data = json.loads(line)
        msg = urllib.unquote(data['message'])
        print(msg)
    except:
        pass


def runTail(logFile):
    cmd = ['tail', '-n', '100', '-f', LOG_FILE]
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE, universal_newlines=True)
    for line in iter(proc.stdout.readline, ''):
        yield line

    proc.stdout.close()
    return_code = proc.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, cmd)


def main():
    p = argparse.ArgumentParser(description='Paradrop log tool')
    p.add_argument('-f',
                   help='Wait for additional data to be appended to the log file when end of file is reached',
                   action='store_true',
                   dest='f')
    args = p.parse_args()

    try:
        if args.f:
            for line in runTail(LOG_FILE):
                parseLine(line)
        else:
            with open(LOG_FILE, "r") as inputFile:
                for line in inputFile:
                    parseLine(line)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()