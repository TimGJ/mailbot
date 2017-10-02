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
import configparser
import glob
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

    MaxSubjectLength  = 200
    MaxFromName       =  80
    MaxFromAddress    =  80
    MaxToName         =  80
    MaxToAddress      =  80

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
            elif self.message['Content-Type'].startswith('multipart/alternative;'):
                self.multipart = True
            else:
                logger.warning('Message UID={} has unknown content type {}'.format(
                        str(self.imapuid), self.message['Content-Type']))
        except KeyError:
            logger.warning('Message UID={} has no content type'.format(self.imapuid))
            
        if type(self.message['To']) is str:
            todetails = email.utils.getaddresses(self.message['To'].split(","))[0]
            self.toname, self.toaddress = todetails[0][:Message.MaxToName], todetails[1][:Message.MaxToAddress]
        else:
            self.toname = None
            self.toaddress = None
            self.errors = True
 
        if type(self.message['From']) is str:
            fromdetails = email.utils.getaddresses(self.message['From'].split(","))[0]
            self.fromname, self.fromaddress = fromdetails[0][:Message.MaxFromName], fromdetails[1][:Message.MaxFromAddress]
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
        
        self.subject = self.message['Subject'][:Message.MaxSubjectLength] if self.message['Subject'] else None

    def __repr__(self):
        return "From: {} {} To: {} {} Date: {} Subject: {}".format(
                self.fromname, self.fromaddress, self.toname, self.toaddress, 
                self.date, self.subject)

class Mailbot:
    """
    Class to hold the details of an instance of the mailbot
    """
    DefaultMailHost   = 'mail.lcn.com'
    DefaultDBUser     = 'mailbot'
    DefaultDBName     = 'asterisk'
    DefaultInterval   =  60
    DefaultMailFolder = 'Inbox'

    def __init__(self, client, cp):
        """
        A class to handle an instance of the mailbot
        :param client: the name of the section of the config file (essentially the client name)
        :param cp: A configparder section object holding the configuration elements for the object
        """
        self.client       = client
        self.dbhost       = cp.get('dbhost')
        self.dbuser       = cp.get('dbuser', Mailbot.DefaultDBUser)
        self.dbpassword   = cp.get('dbpassword')
        self.dbport       = cp.get('dbport')
        self.dbname       = cp.get('dbname', Mailbot.DefaultDBName)
        self.mailhost     = cp.get('mailhost', Mailbot.DefaultMailHost)
        self.mailuser     = cp.get('mailuser')
        self.mailpassword = cp.get('mailpassword')
        self.mailfolder   = cp.get('mailfolder', Mailbot.DefaultMailFolder)
        self.checkall     = cp.getboolean('checkall', False)
        try:
            self.interval = int(cp.get('interval', Mailbot.DefaultInterval))
        except  (TypeError, ValueError) as e:
            logger.error('{}: Interval must be an integer number of seconds: {}'.format(self.client, e))
        self.messages = self.GetMessages()
        self.ProcessMessages()

    def __repr__(self):
        return('[{}] {}/{}:{} -> {}/{}@{}:{}.{}'.format(self.client, self.mailuser, self.StarPass(self.mailpassword),
                                                        self.mailhost, self.dbuser, self.StarPass(self.dbpassword),
                                                        self.dbhost, self.dbport, self.dbname ))

    def StarPass(self, p):
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


    def GetMessages(self):
        """
        Performs an SSL login to an IMAP server and downloads either ALL
        messages (args.fetchall == True) or only new ones (args.fetchall ==
        False).

        Each message it encounters is converted to a Message object and appended
        to a list, which is returned.
        """
        try:
            mail = imaplib.IMAP4_SSL(self.mailhost)
        except imaplib.IMAP4.error as e:
            logger.error("{}: {}".format(self.client, str(e)))
            if re.search('11004|getaddrinfo', str(e)):
                logger.error('{}: Is host {} reachable?'.format(self.client, self.mailhost))
            return
        except socket_error as serr:
            if serr.errno != errno.ECONNREFUSED:
                logger.error('{}: Socket error: {}'.format(self.client, serr))
                raise serr
            else:
                logger.error('{}: Connection refused. Broken firewall/proxy?: {}'.format(self.client, serr))
        else:
            logger.debug('{}: Connecting as {} to {}'.format(self.client, self.mailuser, self.mailhost))
            try:
                mail.login(self.mailuser, self.mailpassword)
            except imaplib.IMAP4.error as e:
                logger.error("{}: IMAP4 Error! {}".format(self.client, str(e)))
                if re.search('AUTHENTICATIONFAILED', str(e)):
                    logger.error('{}: Can user {} connect with password {}?'.format(self.client, self.mailuser,
                                  self.StarPass(self.mailpassword)))
                return

            mail.select(self.mailfolder) # connect to inbox.

            messages = []
            try:
                result, uidstring = mail.search(None, "ALL" if self.checkall else "(UNSEEN)")
            except imaplib.IMAP4.error as e:
                logger.error("{}: {}".format(self.client, str(e)))
                if re.search('SEARCH illegal in state AUTH', str(e)):
                    logger.error('{}: Does folder {} exist?'.format(self.client, self.mailfolder))
                return

            if result == 'OK':
                uids = uidstring[0].split()
                logger.debug("{}: Got {} UIDs".format(self.client, len(uids)))
                for i, uid in enumerate(uids):
                    result, data = mail.fetch(uid,"(RFC822 BODY[TEXT])")
                    if result == 'OK':
                        messages.append(Message(data, uid))
                    else:
                        logger.debug("{}: Failed to fetch UID {}".format(self.client, uid))
            mail.close()
            return messages


    def ProcessMessage(self, message, cursor):
        """
        Processes an individual message.

        Checks to see if the e-mail address already exists in the leads table
        If it does retrives the lead ID, otherwise generates a new one
        """
        logger.debug("{}: Message from {}".format(self.client, message.fromaddress))

        try:
            cursor.execute("select lead_id, title, first_name, last_name from vicidial_list where email = %s",
                       (message.fromaddress))
        except pymysql.err.ProgrammingError as e:
            logger.error('{}: Error: {}'.format(self.client, e))
            logger.error(cursor._last_executed)

        result = cursor.fetchone()
        if result:
            logger.debug('{}: Matched ID {} to {}'.format(self.client, result[0], message.fromaddress))
            lead_id = result[0]
        else:
            logger.debug("{}: Couldn't find leadid for {}".format(self.client, message.fromaddress))
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
                logger.error('{}: Error: {}'.format(self.client, e))
                logger.error(cursor._last_executed)

            cursor.execute('SELECT LAST_INSERT_ID()')
            lead_id = cursor.fetchone()[0]
            logger.debug('{}: Created leadid {} for {}'.format(self.client, lead_id, message.fromaddress))

        # Get inbound group based on incoming e-mail address.

        cursor.execute("select group_id from vicidial_inbound_groups where email = %(to)s",{'to': message.toaddress})

        result = cursor.fetchone()
        group = result[0] if result else "Unknown"

        logger.debug('{}: E-mail group for {} is {}'.format(self.client, message.toaddress, group))
        # Now we have a leadid, create a new record in the database for the
        # e-mail message. We need to populate several fields, some of which
        # may not be present in the message.
        # Variable names are those of the corresponding columns

        try:
            payload = message.message.get_payload()[0].get_payload()
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


    def ProcessMessages(self):
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
            cnx = pymysql.connect(host=self.dbhost, user=self.dbuser,
                                  password=self.dbpassword, database=self.dbname)
        except pymysql.err.OperationalError as e:
            logger.error("{}: MySQL Error: {}".format(self.client, str(e)))
            if re.search('1045|Access denied', str(e)):
                logger.error('{}: Is password {} correct for {}@{}?'.format(
                        self.client, self.StarPass(self.dbpassword), self.dbuser, self.dbhost))
            if re.search('2003|Can\'t connect to MySQL server', str(e)):
                logger.error("{}: Is MySQL reachable at port 3306 on {}?".format(
                        self.client, self.dbhost))
        else:
            cursor = cnx.cursor()
            for message in self.messages:
                self.ProcessMessage(message, cursor)
                cnx.commit()
            cursor.close()
            cnx.close()

if __name__ == '__main__':

    args = ProcessArguments()
    if args.config:
        g = []
        for f in args.config:
            g += [h for h in glob.glob(f)]
        if g:
            cp = configparser.ConfigParser()
            r = cp.read(g)
            try:
                logger = SetupLogger(cp['LOGGING'])
            except KeyError:
                logging.error('Can\'t find [LOGGING] section in config files: {}'.format('; '.join(r)))
            else:
                logger.debug('Python: {}'.format(sys.executable))
                logger.debug('Information: {}'.format(sys.version))
                repo = git.Repo()
                try:
                    ver = repo.git.describe()
                except git.exc.GitCommandError:
                    logger.debug('No git version available')
                else:
                    logger.info('{} Ver. {}'.format(__file__, ver))

                clients = [Mailbot(n, cp[n]) for n in sorted(list(set(cp.sections()) - set(['LOGGING'])))]
                if len(clients):
                    logger.debug('Read config for {} clients: {}'.format(len(clients), "; ".join([c.client for c in clients])))
                else:
                    logger.error('No client configurations found in config files.')
    else:
        logging.error('Can\'t find configuration file(s): {}'.format('; '.join(args.config)))

