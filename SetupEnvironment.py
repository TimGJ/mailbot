"""
Setup a couple of bits of environment necessary for execution of the mailbot.

Separated out in to its own module for reasability
"""

import logging
import logging.handlers
import argparse
import git
import sys
import glob
import configparser
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

    parser.add_argument('--config',       help='Configuration file', nargs='+')

    emlgrp = parser.add_argument_group('E-mail', 'E-mail collection options')
    emlgrp.add_argument('--mailfolder',   help='Folder name', default='Inbox')
    emlgrp.add_argument('--mailpassword', help='Mail password (prompts if blank)')
    emlgrp.add_argument('--mailserver',   help='IMAP server', default = 'mail.lcn.com')
    emlgrp.add_argument('--mailaddress',  help='E-mail address')

    mexgrp = parser.add_mutually_exclusive_group()
    mexgrp.add_argument('--checkall',     help='Process all mails rather than just new ones', action='store_true')
    mexgrp.add_argument('--interval',     help='Seconds between e-mail checks', type=int)

    loggrp = parser.add_argument_group('Logging', 'Options for event logging')
    loggrp.add_argument('--verbose',      help='Verbose output', action='store_true')
    loggrp.add_argument('--logfile',      help='Log file name')
    loggrp.add_argument('--rotate',       help='Rotate log file at start', action='store_true')
    loggrp.add_argument('--backupcount',  help='Number of logs to retain', default = 5, type = int)

    dbgrp = parser.add_argument_group('DB', 'Database options')
    dbgrp.add_argument('--dbuser',       help='Database username', default='mailbot')
    dbgrp.add_argument('--dbpassword',   help='Database passsword (prompts if blank)')
    dbgrp.add_argument('--dbname',       help='Database name', default = 'asterisk')
    dbgrp.add_argument('--dbserver',     help='Database server IP address/hostname')

    args = parser.parse_args()

    if args.logfile and any([args.mailfolder, args.mailpassword, args.mailserver, args.mailaddress,
                             args.checkall, args.interval, args.verbose, args.logfile, args.rotate,
                             args.dbuser. args.dbpassword, args.dbname, args.dbserver]):
        parser.error('--config may not be used with any other options')

    if args.rotate and not args.logfile:
        parser.error('--rotate option can only be used with --logfile')

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
            return int(float(radix) * multipliers[suffix]))
        except (ValueError, TypeError) as err:
            logging.error("Can't interpret {} as a number {}".format(input, err))
    else:
        try:
            return int(input)
        except (ValueError, TypeError) as err:
            logging.error("Can't interpret {} as a number {}".format(input, err))


def SetupLogger(**args):
    """
    Sets up logging. Moved to a separate function for readability. Suppose I should really convert it
    to kwargs format at some stage...
    :param args: **kwargs
    :param backupCount: Maximum number of logfiles to retain
    :return: logger object
    """

    try:
        backupcount = int(args['backupcount'])
    except (TypeError, ValueError) as e:
        print("Backup count must be a positive integer", file=sys.stderr)
    logger = logging.getLogger('mailbot')
    logger.setLevel(logging.DEBUG if args['verbose'] else logging.INFO)
    formatter = logging.Formatter(fmt='%(asctime)s: %(message)s')
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)
    if args['logfile']:
        logfile = logging.handlers.RotatingFileHandler('{}.log'.format(args['logfile']),
                                maxBytes=maxbytes, backupCount=backupcount)
        logfile.setFormatter(formatter)
        logger.addHandler(logfile)
        if args['rotate']: # Rotate the logfile
            logfile.doRollover()
    return logger

if __name__ == '__main__':
    args = ProcessArguments()
    logger = SetupLogger(**vars(args))
    # As we might someday want to run this on Windows or some other broken OS
    # which doesn't support file globbing, then glob each of the entries in the list.
    g = []
    bots = []
    if args.config:
        for f in args.config:
            g += glob.glob(f)
        if g:
            cp = configparser.ConfigParser()
            cp.read(g)



