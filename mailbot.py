#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Created on Thu Jun 22 20:02:33 2017
@author: Tim Greening-Jackson

Simple mailbot. Collects new mail from a particular IMAP mailserver using a
particular set or credentials. Stores new mail in a database. 

Doesn't yet deal with attachments.

"""

import logging
import argparse
import imaplib
import email
import time
import getpass
import pymysql
import re
import git
import sys
import errno
from socket import error as socket_error

class Message:
    """
    Converts a response from the IMAP server in to a Message. Should be
    passed to the constructor as a tuple containing two byte strings. 
    """
    def __init__(self, data, uid):
        self.imapuid = str(uid) if uid else None
        self.data = data
        self.message = email.message_from_bytes(data[0][1])
        self.text = data[1][1].decode('utf-8') if type(data[1][1]) is bytes else None
        self.errors  = False

        try:
            if self.message['Content-Type'].startswith('text/plain'):
                self.multipart = False
            elif self.message['Content-Type'].startswith('multipart/mixed;'):
                self.multipart = True
            else:
                logging.warning('Message UID={} has unknown content type {}'.format(
                        self.imapuid, self.message['Content-Type']))
        except KeyError:
            logging.warning('Message UID={} has no content type'.format(self.imapuid))
            
        if type(self.message['To']) is str:
            todetails = email.utils.getaddresses(self.message['To'].split(","))[0]
            self.toname, self.toaddress = todetails[0], todetails[1]
        else:
            self.toname = None
            self.toaddress = None
            self.errors = True
 
        if type(self.message['From']) is str:
            fromdetails = email.utils.getaddresses(self.message['From'].split(","))[0]
            self.fromname, self.fromaddress = fromdetails[0], fromdetails[1]
        else:
            self.sender = None
            self.errors = True
            print("Error parsing From '{}'".format(self.message['From']))

        try:
            self.date = email.utils.parsedate_to_datetime(self.message['Date'])
        except:
            self.date = None
            self.errors = True
            print("Error parsing date '{}'".format(self.message['Date']))
        
        self.subject = self.message['Subject'] if self.message['Subject'] else None

    def __repr__(self):
        return "From: {} {} To: {} {} Date: {} Subject: {}".format(
                self.fromname, self.fromaddress, self.toname, self.toaddress, 
                self.date, self.subject)
        

def StarPass(p):
    """
    Returns starred form of password p, showing the first and last characters
    with the others replaced by *. So password "swordfish" would return
    "s*******h"
    """
    if type(p) is not str:
        logging.warning("Can't interpret password {}".format(p))
        return
    elif len(p) < 3:
        return p
    else:
        return p[0] + (len(p)-2)*'*' + p[-1]

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

    emlgrp = parser.add_argument_group('E-mail', 'E-mail collection options')
    emlgrp.add_argument('--mailfolder',   help='Folder name', default='Inbox')
    emlgrp.add_argument('--mailpassword', help='Mail password (prompts if blank)')
    emlgrp.add_argument('--mailserver',   help='IMAP server', default = 'mail.lcn.com')
    emlgrp.add_argument('mailaddress',    help='E-mail address')

    mexgrp = parser.add_mutually_exclusive_group()
    mexgrp.add_argument('--checkall',     help='Process all mails rather than just new ones', action='store_true')
    mexgrp.add_argument('--interval',     help='Seconds between e-mail checks (only runs once if ommitted)', type=int)
    parser.add_argument('--verbose',      help='Verbose output', action='store_true')
    parser.add_argument('--logfile',      help='Name of logfile (logs to console otherwise)')

    dbgrp = parser.add_argument_group('DB', 'Database options')
    dbgrp.add_argument('--dbuser',       help='Database username', default='mailbot')
    dbgrp.add_argument('--dbpassword',   help='Database passsword (prompts if blank)')
    dbgrp.add_argument('--dbname',       help='Database name', default = 'asterisk')
    dbgrp.add_argument('dbserver',       help='Database server IP address/hostname')

    return parser.parse_args()

def GetMessages(args):
    """
    Performs an SSL login to an IMAP server and downloads either ALL
    messages (args.fetchall == True) or only new ones (args.fetchall ==
    False).
    
    Each message it encounters is converted to a Message object and appended
    to a list, which is returned.
    """
    try:
        mail = imaplib.IMAP4_SSL(args.mailserver)
    except imaplib.IMAP4.error as e:
        logging.error("{}".format(str(e)))
        if re.search('11004|getaddrinfo', str(e)):
            logging.error('Is host {} reachable?'.format(args.mailserver))
        return
    except socket_error as serr:
        if serr.errno != errno.ECONNREFUSED:
            logging.error('Socket error: {}'.format(serr))
            raise serr
        else:
            logging.error('Connection refused. Broken firewall/proxy? Call Reece!: {}'.format(serr))
    else:
        logging.debug('Connecting as {} to {}'.format(args.mailaddress, args.mailserver))
        try:
            mail.login(args.mailaddress, args.mailpassword)
        except imaplib.IMAP4.error as e:
            logging.error("{}".format(str(e)))
            if re.search('AUTHENTICATIONFAILED', str(e)):
                logging.error('Can user {} connect with pasword {}?'.format(args.address,
                              StarPass(args.mailpassword)))
            return

        mail.select(args.mailfolder) # connect to inbox.

        messages = []
        try:
            result, uidstring = mail.search(None, "ALL" if args.checkall else "(UNSEEN)")
        except imaplib.IMAP4.error as e:
            logging.error("{}".format(str(e)))
            if re.search('SEARCH illegal in state AUTH', str(e)):
                logging.error('Does folder {} exist?'.format(args.folder))
            return

        if result == 'OK':
            uids = uidstring[0].split()
            logging.debug("Got {} UIDs".format(len(uids)))
            for i, uid in enumerate(uids):
                result, data = mail.fetch(uid,"(RFC822 BODY[TEXT])")
                if result == 'OK':
                    messages.append(Message(data, uid))
                else:
                    logging.debug("Failed to fetch UID {}".format(uid))
        mail.close()
        return messages


def ProcessMessage(message, cursor):
    """
    Processes an individual message.
    
    Checks to see if the e-mail address already exists in the leads table
    If it does retrives the lead ID, otherwise generates a new one
    """
    logging.debug("Message from {}".format(message.fromaddress))

    try:    
        cursor.execute("select lead_id, title, first_name, last_name from vicidial_list where email = %s", 
                   (message.fromaddress))
    except pymysql.err.ProgrammingError as e:
        logging.error('Error: {}'.format(e))
        logging.error(cursor._last_executed)
    
    result = cursor.fetchone()
    if result:
        logging.debug('Matched ID {} to {}'.format(result[0], message.fromaddress))
        lead_id = result[0]
    else:
        logging.debug('Couldn''t find leadid for {}'.format(message.fromaddress))
        # Because we only have 30 characters for each of the first and last names,
        # make a stab at separating the punters name in to first and last parts.
        # Also make sure that any apostrophes in the name - e.g. O'Reilly -
        # are doubled as otherwise it would break SQL string parsing.
        parts = message.fromname.split()
        first_name = parts[0].replace("'", "''")
        try:
            last_name = " ".join(parts[1:]).replace("'", "''")
        except IndexError:
            last_name = "UNKNOWN"

        try:
            cursor.execute("insert into vicidial_list (email, first_name, last_name) values (%s, %s, %s)",
                           (message.fromaddress, first_name, last_name))
        except pymysql.err.ProgrammingError as e:
            logging.error('Error: {}'.format(e))
            logging.error(cursor._last_executed)

        cursor.execute('SELECT LAST_INSERT_ID()')
        lead_id = cursor.fetchone()[0]
        logging.debug('Created leadid {} for {}'.format(lead_id, message.fromaddress))

    # Get inbound group based on incoming e-mail maddress.
    
    cursor.execute("select group_id from vicidial_inbound_groups where email = %s", 
                   (message.toaddress))
    
    result = cursor.fetchone()
    group = result[0] if result else "Unknown"

    logging.debug('E-mail group for {} is {}'.format(message.toaddress, group))     
    # Now we have a leadid, create a new record in the database for the 
    # e-mail message. We need to populate several fields, some of which
    # may not be present in the message.
    # Variable names are those of the corresponding columns

    try:
        payload = message.message.get_payload()[0].get_payload().replace('"','""')
    except AttributeError:
        payload = '*** NO MESSAGE CONTENT ***' 

    try:
        cursor.execute("""insert into `vicidial_email_list` (
                `lead_id`, `protocol`, `email_date`, `email_to`, 
                `email_from`, `email_from_name`, `subject`, `mime_type`, 
                `content_type`, `content_transfer_encoding`, `x_mailer`, 
                `sender_ip`, `message`, `email_account_id`, `group_id`, 
                `status`, `direction`) values (%s, %s, %s, %s, 
                %s, %s, %s, %s, %s, %s,  %s, %s, %s, %s, %s, %s, %s) """
                ,(lead_id, 
                "IMAP",
                message.date.strftime('%Y-%m-%d %H:%M:%S'), 
                message.message['to'], message.fromaddress, 
                message.fromname, message.subject, 
                message.message['mime-type'], 
                message.message['content-type'].split(';')[0], 
                message.message['content-transfer-encoding'],
                message.message['x-mailer'], message.message['sender-ip'], 
                payload, "MAILBOT", group, "NEW", "INBOUND"))
    except pymysql.err.ProgrammingError as e:
        logging.error('Error: {}'.format(e))
        logging.error(cursor._last_executed)

    
def ProcessMessages(messages, args):
    """
    Adds details of the downloaded mail messages.
    
    Connects to the database as specified with credentials in args.
    For each message in the list of messages, calls ProcessMessage()
    Cleans up after itself
    Returns None
    
    NOTE by default autocommit is turned off, so in case we get two messages
    in the same session from the same user we commit after processing each
    message...
    """
    try:
        cnx = pymysql.connect(host=args.dbserver, user=args.dbuser, 
                              password=args.dbpassword, database=args.dbname)
    except pymysql.err.OperationalError as e:
        logging.error("MySQL Error: {}".format(str(e)))
        if re.search('1045|Access denied', str(e)):
            logging.error('Is password {} correct for {}@{}?'.format(
                    StarPass(args.dbpassword), args.dbuser, args.dbserver))
        if re.search('2003|Can''t connect to MySQL server', str(e)):
            logging.error('Is MySQL reachable at port 3306 on {}?'.format(
                    args.dbserver))
    else:
        cursor = cnx.cursor()
        for message in messages:
            ProcessMessage(message, cursor)
            cnx.commit()
        cursor.close()
        cnx.close()

if __name__ == '__main__':

    args = ProcessArguments()
    if args.logfile:
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, filename=args.logfile,l
                            format='%(asctime)s:%(levelname)s:%(message)s')
    else:
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stdout,
                            format='%(asctime)s:%(levelname)s:%(message)s')
    repo = git.Repo()
    try:
        ver = repo.git.describe()
    except git.exc.GitCommandError:
        ver = "** NO GIT TAG DEFINED **"
    logging.info('{} Info: {}'.format(sys.executable, sys.version))
    logging.info('{} Ver. {}'.format(__file__, ver))

    if not args.mailpassword:
        args.mailpassword = getpass.getpass('Mail password: ')
    if not args.dbpassword:
        args.dbpassword = getpass.getpass('Database password: ')

    # Get the mail headers

    try:
        while True:
            messages = GetMessages(args)
            if messages:
                ProcessMessages(messages, args)
            if args.interval:
                time.sleep(args.interval)
            else:
                logging.info("No repeat interval specified. Exiting")
                break
    except KeyboardInterrupt:
        logging.info('Bye!')
