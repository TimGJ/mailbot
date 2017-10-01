"""
Setup a couple of bits of environment necessary for execution of the mailbot.

Separated out in to its own module for reasability
"""

import logging
import logging.handlers
import argparse
import git
import sys
import re

def ProcessArguments():
    """
    Process the command line arguments returning an argparse namepsace
    or None if the parse didn't work.
    """
    repo = git.Repo()
    try:
        ver = repo.git.describe()
    except git.exc.GitCommandError:
        ver = None
    description = 'Connex Mailbot'
    if ver:
        description += ' v. {}'.format(ver)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--config', help='Configuration file', nargs='+', default = 'mailbot.conf')
    args = parser.parse_args()
    return args

def GetSize(input):
    """
    Converts string (input) in to a number and multiplies by any suffix appended to it
    :param input: string
    :return: float
    """


    multipliers = {'B': 1 << 0, 'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30, 'T': 1 << 40, 'E': 1 << 50}

    pattern = r'([0-9.]+)([KMGTE])'
    match = re.match(pattern, input.upper())
    if match:
        (radix, suffix) = match.groups()
        try:
            return int(float(radix) * multipliers[suffix])
        except (ValueError, TypeError) as err:
            logging.error("Can't interpret {} as a number {}".format(input, err))
    else:
        try:
            return int(input)
        except (ValueError, TypeError) as err:
            logging.error("Can't interpret {} as a number {}".format(input, err))


def SetupLogger(cnf):
    """
    Set up logging. Logging is to a rotating logfile and can also be to the console if the
    configration option console = True
    :param cnf: ConfigParser
    :return:
    """
    DefaultBackupCount = 5 # Default number of log backups
    DefaultMaxBytes = '1M' # Default max size of logfile

    logger = logging.getLogger(cnf.get('LogName', 'mailbot'))
    levelname = cnf.get('loglevel', 'INFO').upper()
    try:
        level = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
                 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}[levelname]
    except KeyError:
        logging.error('Unknown logging level {}. Using logging.INFO instead.'.format(levelname))
        level = logging.INFO

    logger.setLevel(level)
    formatter = logging.Formatter(fmt='%(asctime)s: %(message)s')
    if cnf.getboolean('console', False):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        logger.addHandler(console)

    maxbytes = GetSize(cnf.get('MaxBytes', DefaultMaxBytes))
    if not maxbytes:
        logging.error("Can't interpret MaxBytes value {}. Using {} instead.".format(cnf.get('MaxBytes'), DefaultMaxBytes))
        maxbytes = GetSize(DefaultMaxBytes)

    try:
        backupcount = int(cnf.get('BackupCount', DefaultBackupCount))
    except (ValueError):
        logging.error('BackupCount value must be an integer. Got {}. Using {} instead.'.format(cnf.get('BackupCount'), DefaultBackupCount))
        backupcount = DefaultBackupCount
    logfile = logging.handlers.RotatingFileHandler(cnf.get('LogFile', 'mailbot.log'), maxBytes=maxbytes, backupCount=backupcount)
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)
    if cnf.getboolean('RotateOnStartup', False): # Rotate the logfile
        logfile.doRollover()
    return logger

if __name__ == '__main__':
    pass
