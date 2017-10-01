#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Created on Thu Jun 22 20:02:33 2017
@author: Tim Greening-Jackson

Simple mailbot. Collects new mail from a particular IMAP mailserver using a
particular set or credentials. Stores new mail in a database. 

Doesn't yet deal with attachments.

"""

import imaplib
import email
import time
import getpass
import re
import sys
import errno
from socket import error as socket_error

try:
    import pymysql
except ModuleNotFoundError:
    print('Error! Requires PyMySQL installing in order to run', file=sys.stderr)
    sys.exit(1)

try:
    import git
except ModuleNotFoundError:
    print('Error! Requires GitPython installing in order to run', file=sys.stderr)
    sys.exit(1)

if sys.version_info.major < 3:
    print('Error! Requires Python 3 in order to run. Found {}'.format(sys.version), file=sys.stderr)
    sys.exit(1)


from SetupEnvironment import *

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
                logger.warning('Message UID={} has unknown content type {}'.format(
                        self.imapuid, self.message['Content-Type']))
        except KeyError:
            logger.warning('Message UID={} has no content type'.format(self.imapuid))
            
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

class Mailbot:
    """
    Class to hold the details of an instance of the mailbot
    """

    def __init__(self, jobname, dbhost, dbuser, dbpassword, dbport, dbname, mailhost, mailuser, mailpassword, interval):
        """
        :param jobname: Some identifying string used in logger messages
        :param dbhost:  Database host (e.g. 'merlin' or '10.20.30.40')
        :param dbuser: Database user (e.g. 'mailbot')
        :param dbpassword: Datgabase password
        :param dbport: Datbase port (e.g. 3306)
        :param dbname: Name of the database (e.g. asterisk)
        :param mailhost: IMAPS mail server (e.g. mail.lcn.com)
        :param mailuser: username on mailserver (e.g. tim@xyzzy.com)
        :param mailpassword: IMAP password (e.g. 'swordfish')
        :param interval: Interval in seconds betwen runs. Zero means it only runs once.
        """
        self.jobname = jobname
        self.dbhost = dbhost
        self.dbuser = dbuser
        self.dbpassword = dbpassword
        self.dbport = dbport
        self.dbname = dbname
        self.mailhost = mailhost
        self.mailuser = mailuser
        self.mailpassword = mailpassword
        try:
            self.interval = int(interval)
        except  (TypeError, ValueError) as e:
            logger.error('{} Interval must be an integer number of seconds: {}'.format(self.jobname, e))



def StarPass(p):
    """
    Returns starred form of password p, showing the first and last characters
    with the others replaced by *. So password "swordfish" would return
    "s*******h"
    """
    if type(p) is not str:
        logger.warning("Can't interpret password {}".format(p))
        return
    elif len(p) < 3:
        return p
    else:
        return p[0] + (len(p)-2)*'*' + p[-1]


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
        logger.error("{}".format(str(e)))
        if re.search('11004|getaddrinfo', str(e)):
            logger.error('Is host {} reachable?'.format(args.mailserver))
        return
    except socket_error as serr:
        if serr.errno != errno.ECONNREFUSED:
            logger.error('Socket error: {}'.format(serr))
            raise serr
        else:
            logger.error('Connection refused. Broken firewall/proxy? Call Reece!: {}'.format(serr))
    else:
        logger.debug('Connecting as {} to {}'.format(args.mailaddress, args.mailserver))
        try:
            mail.login(args.mailaddress, args.mailpassword)
        except imaplib.IMAP4.error as e:
            logger.error("IMAP4 Error! {}".format(str(e)))
            if re.search('AUTHENTICATIONFAILED', str(e)):
                logger.error('Can user {} connect with pasword {}?'.format(args.mailaddress,
                              StarPass(args.mailpassword)))
            return

        mail.select(args.mailfolder) # connect to inbox.

        messages = []
        try:
            result, uidstring = mail.search(None, "ALL" if args.checkall else "(UNSEEN)")
        except imaplib.IMAP4.error as e:
            logger.error("{}".format(str(e)))
            if re.search('SEARCH illegal in state AUTH', str(e)):
                logger.error('Does folder {} exist?'.format(args.folder))
            return

        if result == 'OK':
            uids = uidstring[0].split()
            logger.debug("Got {} UIDs".format(len(uids)))
            for i, uid in enumerate(uids):
                result, data = mail.fetch(uid,"(RFC822 BODY[TEXT])")
                if result == 'OK':
                    messages.append(Message(data, uid))
                else:
                    logger.debug("Failed to fetch UID {}".format(uid))
        mail.close()
        return messages


def ProcessMessage(message, cursor):
    """
    Processes an individual message.
    
    Checks to see if the e-mail address already exists in the leads table
    If it does retrives the lead ID, otherwise generates a new one
    """
    logger.debug("Message from {}".format(message.fromaddress))

    try:    
        cursor.execute("select lead_id, title, first_name, last_name from vicidial_list where email = %s", 
                   (message.fromaddress))
    except pymysql.err.ProgrammingError as e:
        logger.error('Error: {}'.format(e))
        logger.error(cursor._last_executed)
    
    result = cursor.fetchone()
    if result:
        logger.debug('Matched ID {} to {}'.format(result[0], message.fromaddress))
        lead_id = result[0]
    else:
        logger.debug('Couldn''t find leadid for {}'.format(message.fromaddress))
        # Because we only have 30 characters for each of the first and last names,
        # make a stab at separating the punters name in to first and last parts.

        parts = message.fromname.split()
        first_name = parts[0]
        try:
            last_name = " ".join(parts[1:])
        except IndexError:
            last_name = "UNKNOWN"
        try:
            cursor.execute("insert into vicidial_list (email, first_name, last_name) values (%s, %s, %s)",
                           (message.fromaddress, first_name, last_name))
        except pymysql.err.ProgrammingError as e:
            logger.error('Error: {}'.format(e))
            logger.error(cursor._last_executed)

        cursor.execute('SELECT LAST_INSERT_ID()')
        lead_id = cursor.fetchone()[0]
        logger.debug('Created leadid {} for {}'.format(lead_id, message.fromaddress))

    # Get inbound group based on incoming e-mail maddress.
    
    cursor.execute("select group_id from vicidial_inbound_groups where email = %s", 
                   (message.toaddress))
    
    result = cursor.fetchone()
    group = result[0] if result else "Unknown"

    logger.debug('E-mail group for {} is {}'.format(message.toaddress, group))
    # Now we have a leadid, create a new record in the database for the 
    # e-mail message. We need to populate several fields, some of which
    # may not be present in the message.
    # Variable names are those of the corresponding columns

    try:
        payload = message.message.get_payload()[0].get_payload().replace('"','""')
    except AttributeError:
        payload = '*** NO MESSAGE CONTENT ***' 

    try:
        cursor.execute("insert into `vicidial_email_list` ("
                       "`lead_id`, `protocol`, `email_date`, `email_to`, "
                       "`email_from`, `email_from_name`, `subject`, `mime_type`, "
                       "`content_type`, `content_transfer_encoding`, `x_mailer`, "
                       "`sender_ip`, `message`, `email_account_id`, `group_id`, "
                       "`status`, `direction`) values (%s, %s, %s, %s, "
                       " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                       , (lead_id,
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
        logger.error('Error: {}'.format(e))
        logger.error(cursor._last_executed)

    
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
        logger.error("MySQL Error: {}".format(str(e)))
        if re.search('1045|Access denied', str(e)):
            logger.error('Is password {} correct for {}@{}?'.format(
                    StarPass(args.dbpassword), args.dbuser, args.dbserver))
        if re.search('2003|Can''t connect to MySQL server', str(e)):
            logger.error('Is MySQL reachable at port 3306 on {}?'.format(
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
    logger=SetupLogger(**vars(args))
    repo = git.Repo()
    try:
        ver = repo.git.describe()
    except git.exc.GitCommandError:
        ver = "** NO GIT TAG DEFINED **"
    logger.info('{} Info: {}'.format(sys.executable, sys.version))
    logger.info('{} Ver. {}'.format(__file__, ver))

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
                logger.info("No repeat interval specified. Exiting")
                break
    except KeyboardInterrupt:
        logger.info('Bye!')
