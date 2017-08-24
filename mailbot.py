
# -*- coding: utf-8 -*-
"""
Created on Thu Jun 22 20:02:33 2017
@author: Tim Greening-Jackson
Simplistic mailbot to demonstrate collection and processing
of e-mail

"""

import logging
import argparse
import imaplib
import email
import time
import getpass
import pymysql

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
                logging.warning('Message UID={} has unknown content type {}'.format(self.imapuid, self.message['Content-Type']))
        except KeyError:
            logging.warning('Message UID={} has no content type'.format(self.imapuid))
            
        for k in self.message.keys():
            logging.debug('message[{}]: {}'.format(k, self.message[k]))

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
        

def ProcessArguments():
    """
    Process the command line arguments returning an argparse namepsace
    or None if the parse didn't work.
    """
    parser = argparse.ArgumentParser(description='Proof of concept mailbot')
    parser.add_argument('--folder',       help='Folder name', default='Inbox')
    parser.add_argument('--checkall',     help='Process all mails rather than just new ones', action='store_true')
    parser.add_argument('--reply',        help='Automatically reply to incoming e-mail', action='store_true')
    parser.add_argument('--interval',     help='Seconds between e-mail checks (only runs once if ommitted)', type=int)
    parser.add_argument('--verbose',      help='Verbose output', action='store_true')
    parser.add_argument('--mailpassword', help='Mail password (prompts if blank)')
    parser.add_argument('--dbpassword',   help='Database passsword (prompts if blank)')
    parser.add_argument('address',        help='E-mail address')
    parser.add_argument('mailserver',     help='IMAP server')
    parser.add_argument('dbserver',       help='Database server')
    parser.add_argument('dbuser',         help='Database username')
    parser.add_argument('dbname',         help='Database name')

    return parser.parse_args()

def GetMessages(args):
    """
    Performs an SSL login to an IMAP server and downloads either ALL
    messages (args.fetchall == True) or only new ones (args.fetchall ==
    False).
    
    Each message it encounters is converted to a Message object and appended
    to a list, which is returned.
    """
    mail = imaplib.IMAP4_SSL(args.mailserver)
    logging.debug('Connecting as {} to {}'.format(args.address, args.mailserver))
    mail.login(args.address, args.mailpassword)

    mail.select(args.folder) # connect to inbox.

    messages = []
    if args.checkall:
        result, uidstring = mail.search(None, "ALL")
    else:
        result, uidstring = mail.search(None, "(UNSEEN)")
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


def GenerateVicidialEmailListSQL(message, lead_id, group):
    """
    Returns a suitably formed SQL query for insertion of a message in to the
    vicidial_email_list table. Separated out for tidiness and readability.
    """
    
    # Get the payload and strip various naughty charaqcters from it
    # as they might break the SQL insertion
    try:
        payload = message.message.get_payload()[0].get_payload().replace('"','""')
    except AttributeError:
        payload = '*** NO MESSAGE CONTENT ***' 
        
    sql = """
        insert into vicidial_email_list (
                lead_id, 
                protocol, 
                email_date, 
                email_to, 
                email_from, 
                email_from_name, 
                subject, 
                mime_type, 
                content_type, 
                content_transfer_encoding, 
                x_mailer, 
                sender_ip, 
                message, 
                email_account_id, 
                group_id, 
                status, 
                direction)
        values ({}, "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", 
                "{}", "{}", "{}", "{}", "{}", "{}", "{}")
        """.format(
    lead_id, 
    "IMAP",
    message.date.strftime('%Y-%m-%d %H:%M:%S'), 
    message.message['to'].replace('"',''),
    message.fromaddress.replace('"',''), 
    message.fromname.replace('"',''), 
    message.subject.replace('"',''), 
    message.message['mime-type'], 
    message.message['content-type'].split(';')[0], 
    message.message['content-transfer-encoding'],
    message.message['x-mailer'],
    message.message['sender-ip'], 
    payload,
    "MAILBOT", 
    group, 
    "NEW",
    "INBOUND"
    )
        
    return sql

def ProcessMessage(message, cursor):
    """
    Processes an individual message.
    
    Checks to see if the e-mail address already exists in the leads table
    If it does retrives the lead ID, otherwise generates a new one
    """
    logging.debug("Message from {}".format(message.fromaddress))
    
    sql = "select lead_id, title, first_name, last_name from vicidial_list where email = '{}'".format(message.fromaddress)
    cursor.execute(sql)
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
            
        sql = "insert into vicidial_list (email, first_name, last_name) values ('{}', '{}', '{}')".format(
                message.fromaddress, first_name, last_name)
        logging.debug(sql)
        cursor.execute(sql)
        cursor.execute('SELECT LAST_INSERT_ID()')
        lead_id = cursor.fetchone()[0]
        logging.debug('Created leadid {} for {}'.format(lead_id, message.fromaddress))

    # Get inbound group based on incoming e-mail maddress.
    
    sql = "select group_id from vicidial_inbound_groups where email = '{}'".format(message.toaddress)
    logging.debug(sql)
    
    cursor.execute(sql)
    result = cursor.fetchone()
    group = result[0] if result else "Unknown"

    logging.debug('E-mail group for {} is {}'.format(message.toaddress, group))     
    # Now we have a leadid, create a new record in the database for the 
    # e-mail message. We need to populate several fields, some of which
    # may not be present in the message.
    # Variable names are those of the corresponding columns
    
    sql = GenerateVicidialEmailListSQL(message, lead_id, group)    
    logging.debug(sql)
    cursor.execute(sql)
    
    

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
    cnx = pymysql.connect(host=args.dbserver, user=args.dbuser, 
                          password=args.dbpassword, database=args.dbname)
    cursor = cnx.cursor()
    for message in messages:
        ProcessMessage(message, cursor)
        cnx.commit()
    cursor.close()
    cnx.close()

if __name__ == '__main__':

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s:%(levelname)s:%(message)s')
    args = ProcessArguments()
    
    if not args.mailpassword:
        args.mailpassword = getpass.getpass('Mail password: ')
    if not args.dbpassword:
        args.dbpassword = getpass.getpass('Database password: ')

    # Get the mail headers

    while True:
        messages = GetMessages(args)
        ProcessMessages(messages, args)
        if args.interval:
            time.sleep(args.interval)
        else:
            logging.debug("No repeat interval specified. Exiting")
            break

