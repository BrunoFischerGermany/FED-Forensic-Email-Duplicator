"""
Licensed under MIT License, (c) B. Fischer
"""
import argparse
from datetime import datetime
from dateutil import parser
import dns.resolver
import chardet
import codecs
import email
from email.header import decode_header
import hashlib
import imapclient
from imapclient import imap_utf7
import logging
import os
import platform
import pickle
import re
import requests
import ssl
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
def clear_subject_for_filename(input_str):
    # replace spaces with underscores
    output_str = input_str.replace(" ", "_")
    output_str = output_str.replace("\\n", "")
    output_str = output_str.replace("\\r", "")
    # Replace all characters that are not letters or numbers with nothing
    output_str = re.sub(r'[^A-Za-z0-9_-]', '', output_str)
    if len(output_str) == 0:
        output_str = "email without subject"
    return output_str
def check_filename(path, filename):
    # Split the filename into its base name and extension
    basename, extension = os.path.splitext(filename)
    if len(basename) > 150:
        basename = basename[:150]
    # Check if the file already exists
    if os.path.exists(os.path.join(path, filename)):
        # If the file exists, find the highest index for which a file with the same base name exists
        index = 1
        while True:
            indexed_filename = f"{basename}.{index}{extension}"
            if not os.path.exists(os.path.join(path, indexed_filename)):
                break
            index += 1
        # Return the next available filename
        return indexed_filename
    else:
        # If the file does not exist, return the original filename
        return filename
def extract_email_data(email_path):
    with codecs.open(email_path, 'rb') as fhdl:
        raw_email = fhdl.read()

    email_message = email.message_from_bytes(raw_email)

    if "Subject" not in email_message:
        subject = "email without subject"
    elif decode_header(email_message["Subject"]) is None:
        subject = "email without subject"
    elif len(decode_header(email_message["Subject"])) == 0:
        subject = "email without subject"
    else:
        subject = decode_header(email_message["Subject"])[0][0]
    if len(subject) == 0:
        subject = "email without subject"

    if isinstance(subject, bytes):
        result = chardet.detect(subject)
        charset = result['encoding']
        # chardet detect windows-1254 but it is utf-8
        try:
            subject = subject.decode(charset.lower())
        except:
            subject = subject.decode('utf-8')

    if "From" not in email_message:
        sender = "email without from"
    elif decode_header(email_message["From"]) is None:
        sender = "email without from"
    elif len(decode_header(email_message["From"])) == 0:
        sender = "email without from"
    else:
        sender = decode_header(email_message["From"])[0][0]
    if isinstance(sender, bytes):
        result = chardet.detect(sender)
        charset = result['encoding']
        if charset is not None:
            sender = sender.decode(charset)
        else:
            sender = sender.decode()

    if "To" not in email_message:
        receiver = "email without to"
    elif decode_header(email_message["To"]) is None:
        receiver = "email without receiver"
    elif len(decode_header(email_message["To"])) == 0:
        receiver = "email without to"
    else:
        receiver = decode_header(email_message["To"])[0][0]
    if isinstance(receiver, bytes):
        result = chardet.detect(receiver)
        charset = result['encoding']
        if charset is not None:
            receiver = receiver.decode(charset)
        else:
            receiver = receiver.decode()

    if "Date" not in email_message:
        date = "1900-01-01 00:00:00 +0100"
    elif decode_header(email_message["Date"]) is None:
        date = "1900-01-01 00:00:00 +0100"
    elif len(decode_header(email_message["Date"])) == 0:
        date = "1900-01-01 00:00:00 +0100"
    else:
        date = decode_header(email_message["Date"])[0][0]
    if isinstance(date, bytes):
        date = date.decode()
    subject = re.sub(r'[\n\r\t\v\f]', '', subject)
    sender = re.sub(r'[\n\r\t\v\f]', '', sender)
    receiver = re.sub(r'[\n\r\t\v\f]', '', receiver)

    return subject, sender, receiver, date
def extract_charset(content_type):
    match = re.search('charset=([\w-]+)', content_type)
    if match:
        return match.group(1)
    else:
        return 'utf-8'
def formatduration(duration):
    if duration >= 3600:
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        duration_str = f"{hours:02}:{minutes:02}:{seconds:02}"
    elif duration >= 60:
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        duration_str = f"{minutes:02}:{seconds:02}"
    else:
        duration_str = f"{duration:.2f}s"

    return duration_str
def get_imap_settings(domain):

    # Retrieve the web page for the given domain
    url = f"https://autoconfig.thunderbird.net/v1.1/{domain}"
    response = requests.get(url)
    imapurl = "-empty-value-"
    sslport = 993

    if response.status_code != 200:
        return imapurl, sslport
    else:
        # extract url for XML-configurationsfile
        root = ET.fromstring(response.content)
        for incoming_server in root.findall("./emailProvider/incomingServer[@type='imap']"):
            if incoming_server.find("socketType").text == "SSL":
                imapurl = incoming_server.find("hostname").text
                sslport = incoming_server.find("port").text
                return imapurl, sslport
    return imapurl, sslport

def validate_email(email):
    # Check the format of the email address with a regular expression
    pattern = r"^[\w\d!#$%&'*+\-/=?^_`{|}~\.]+@([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        return False

    # Check if the domain exists by performing a DNS query
    domain = email.split("@")[1]
    try:
        dns.resolver.resolve(domain, 'MX')
    except dns.resolver.NXDOMAIN:
        return False

    # Extract the domain from the email address
    domain = urlparse("http://" + domain).netloc.split(":")[0]
    return domain
def convert_size(size_bytes):
    # Define the names of the measurement units and their sizes in bytes
    units = ["B", "KB", "MB", "GB", "TB"]
    sizes = [1024 ** i for i in range(len(units))]

    # Find the appropriate unit of measurement
    for i, size in enumerate(sizes[::-1]):
        if size_bytes >= size:
            return f"{round(size_bytes / size, 2)} {units[len(units) - i - 1]}"

    # If the size is smaller than 1 byte
    return f"{size_bytes} B"

def calculate_file_md5(filename):
    with open(filename, 'rb') as f:
        md5_hash = hashlib.md5()
        while chunk := f.read(8192):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def get_linux_version():
    output = subprocess.check_output(['lsb_release', '-a']).decode('utf-8')
    version = None
    for line in output.split('\n'):
        if 'Release' in line:
            version = line.split(':')[1].strip()
            break
    return version

def get_macos_version():
    return platform.mac_ver()[0]

def get_windows_version():
    return platform.win32_ver()[1]
def delete_last_lines(n=1):
    # Delete n lines
    for _ in range(n):
        # Move the cursor to the beginning of the line
        sys.stdout.write('\033[F')
        # Delete the line with spaces
        sys.stdout.write('\033[K')

def export_email(username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend, osSystem, osVersion, login_name):
    global lines
    starttime = time.time()
    ssl_context = ssl.create_default_context()

    # don't check if certificate hostname doesn't match target hostname
    ssl_context.check_hostname = False

    # don't check if the certificate is trusted by a certificate authority
    ssl_context.verify_mode = ssl.CERT_NONE
    now = datetime.now()
    folder_path = os.path.join(output, now.strftime("%Y-%m-%d_%H-%M-%S"))
    # Folder create
    os.makedirs(folder_path)
    if os.path.exists(folder_path) and os.access(folder_path, os.W_OK):
        print(f'The backup path "{folder_path}" exists and is writeable.')
        lines += 1
        output = folder_path
    else:
        print(f'The backup path "{folder_path}" could not be created.')
        lines += 1
        exit()
    logging_file = output + '/' + now.strftime("%Y-%m-%d_%H-%M-%S") + '.txt'
    if os.path.exists(logging_file):
        print(f'The log file already {logging_file} exists and will be overwritten.')
    else:
        print(f'The log file  {logging_file} will created.')
    lines += 1
    if rangebegin == "-empty-value-" and rangeend == "-empty-value-":
        range_string = "ALL"
        range_string_logfile = "Time-Range: ALL emails"
    if rangebegin != "-empty-value-" and rangeend == "-empty-value-":
        begin_obj = parser.parse(rangebegin)
        start_date_str = begin_obj.strftime('%d-%b-%Y')
        range_string = '(SINCE {0})'.format(start_date_str)
        range_string_logfile = f"Time-Range: all emails SINCE {begin_obj}"
    if rangebegin == "-empty-value-" and rangeend != "-empty-value-":
        end_obj = parser.parse(rangeend)
        end_date_str = end_obj.strftime('%d-%b-%Y')
        range_string = '(BEFORE {0})'.format(end_date_str)
        range_string_logfile = f"Time-Range: all emails BEFORE {end_obj}"
    if rangebegin != "-empty-value-" and rangeend != "-empty-value-":
        begin_obj = parser.parse(rangebegin)
        start_date_str = begin_obj.strftime('%d-%b-%Y')
        end_obj = parser.parse(rangeend)
        end_date_str = end_obj.strftime('%d-%b-%Y')
        range_string = '(SINCE {0} BEFORE {1})'.format(start_date_str, end_date_str)
        range_string_logfile = f"Time-Range: all emails SINCE {begin_obj} BEFORE {end_obj}"
    startText = f"""{programTitle}
case number: {case}
evidence number: {evidence}
examiner: {examiner}

IMAP Credentials
Username / Email-address: »{username}«
Password: »{password}«
IMAP-Server: »{imapurl}«
IMAP-SSL-Port: »{sslport}«
{range_string_logfile}

operating system: {osSystem}
operating system version: {osVersion}
computer user: {login_name}

"""
    with open(logging_file, 'w', encoding='utf-8') as f:
        f.write(startText)
    logging.basicConfig(filename=logging_file, level=logging.INFO, encoding='utf-8',
                        format='%(asctime)s - %(levelname)s - %(message)s')

    logging.info('Internet connection exists.')
    logging.info(f'The backup path "{folder_path}" exists and is writeable.')

    email_folder_path = os.path.join(output, username)
    os.makedirs(email_folder_path, exist_ok=True)
    logging.info(f'The email-Folder "{email_folder_path}" was created.')

    imap_server = imapclient.IMAPClient(host=imapurl, port=sslport, ssl_context=ssl_context)
    try:
        # Execute authentication
        imap_server.login(username, password)
        imap_server.select_folder('INBOX', readonly=True)

        folder_list = imap_server.list_folders()
        for folder in folder_list:
            decoded_folder = imap_utf7.decode(folder[2])
            # Create the folder structure in the destination directory
            imap_sub_folder = os.path.join(email_folder_path, decoded_folder)
            os.makedirs(imap_sub_folder, exist_ok=True)
            logging.info(f"Folder {imap_sub_folder} created.")
    except Exception as e:
        logging.error(f"Error when using imapclient: {e}")
        # print(f"Error when using imapclient: {e} ")

    finally:
        # Close connection to IMAP server
        imap_server.logout()
    with open(logging_file, 'a', encoding='utf-8') as f:
        f.write("\nOutput of the created folder structure\n")

    print_directory_tree(email_folder_path, logging_file, output)
    with open(logging_file, 'a', encoding='utf-8') as f:
        f.write("\nReading the number and size of emails in the folders\n")
    total_email = 0
    total_size = 0
    imap_server = imapclient.IMAPClient(host=imapurl, port=sslport, ssl_context=ssl_context)
    try:
        imap_server.login(username, password)
        folder_list = imap_server.list_folders()
        for folder in folder_list:
            try:
                decoded_folder = imap_utf7.decode(folder[2])
                print(f'Reading folder »{decoded_folder}«')
                select_result = imap_server.select_folder(decoded_folder, readonly=True)
                if select_result is None:
                    logging.error(f"Could not select folder »{decoded_folder}«")
                    continue
                else:
                    # if range_string == "ALL":
                    #     email_ids = imap_server.search('ALL', None)
                    # else:
                    # email_ids = imap_server.search(None, f'{range_string}')
                    email_ids = imap_server.search(f'{range_string}', None)
                    if not email_ids:
                        print(f'No emails in folder »{decoded_folder}«.')
                        logging.info(f'no emails found in the folder »{decoded_folder}«')
                    else:
                        folder_total_size = 0
                        email_count = len(email_ids)
                        total_email += email_count
                        email_count_format = '{:,.0f}'.format(email_count)
                        print(f'found {email_count_format} emails in folder »{decoded_folder}«.')
                        for email_id in email_ids:
                            print(f'Checking size of folder »{decoded_folder}«, currently: {convert_size(folder_total_size)}')
                            email_data = imap_server.fetch(email_id, ['RFC822.SIZE'])
                            email_size = email_data[email_id][b'RFC822.SIZE']
                            folder_total_size += email_size
                            total_size += email_size
                            delete_last_lines(1)
                            continue
                        logging.info(f'{email_count_format} emails [Size: {convert_size(folder_total_size)}] found in the folder »{decoded_folder}«.')
                    delete_last_lines(1)
                delete_last_lines(1)
            except Exception as e:
                logging.error(f"Error when processing folder {decoded_folder}: {e}")
    except Exception as e:
        logging.error(f"Error when using imapclient: {e}")
    finally:
        total_email_format = '{:,.0f}'.format(total_email)
        logging.info(f"{total_email_format} emails [Size: {convert_size(total_size)}] found in the mailbox")
        print(f"{total_email_format} emails [Size: {convert_size(total_size)}] found in the mailbox")
        lines += 1
        imap_server.logout()
    with open(logging_file, 'a', encoding='utf-8') as f:
        f.write("\nDownload all Emails to eml\n")
        f.write(f"\n{range_string_logfile}\n")
    total_email_download = 0
    imap_server = imapclient.IMAPClient(host=imapurl, port=sslport, ssl_context=ssl_context)
    try:
        imap_server.login(username, password)
        folder_list = imap_server.list_folders()
        for folder in folder_list:
            try:
                decoded_folder = imap_utf7.decode(folder[2])
                print(f'Downloading email from folder »{decoded_folder}«')
                select_result = imap_server.select_folder(decoded_folder, readonly=True)
                if select_result is None:
                    logging.error(f"Could not select folder »{decoded_folder}«")
                    continue
                else:
                    # if range_string == "":
                    #     email_ids = imap_server.search('ALL', None)
                    # else:
                    email_ids = imap_server.search(f'{range_string}', None)
                    # email_ids = imap_server.search(None, range_string)
                    if not email_ids:
                        logging.info(f'no emails found in the folder »{decoded_folder}«\n')
                    else:
                        folder_total_size = 0
                        email_count = len(email_ids)
                        total_email_download += email_count
                        email_count_format = '{:,.0f}'.format(email_count)
                        imap_sub_folder = os.path.join(email_folder_path, decoded_folder)
                        filecounter = 0
                        for email_id in email_ids:
                            raw_email = imap_server.fetch([email_id], ['RFC822'])[email_id][b'RFC822']
                            result = chardet.detect(raw_email)
                            charset = result['encoding']
                            try:
                                raw_email_string = raw_email.decode(charset.lower())
                            except:
                                raw_email_string = raw_email.decode('utf-8')

                            # Create a filename for the EML file
                            email_id_nr = str(email_id)
                            email_filename = f"{imap_sub_folder}\Message_{email_id_nr.zfill(6)}.eml"
                            # Write the email message to a file
                            filecounter += 1
                            print(f"{filecounter} of {email_count}")
                            print(f"Download Message: {email_filename.replace(output, '')}")
                            with codecs.open(email_filename, "w", encoding='utf-8') as f:
                                f.write(raw_email_string)

                            subject, sender, receiver, msg_date = extract_email_data(email_filename)
                            delete_last_lines(1)
                            file_md5 = calculate_file_md5(email_filename)
                            print(f"MD5 Hash Message: {email_filename.replace(output, '')}")
                            file_size = os.path.getsize(email_filename)
                            email_filename_new = f"{clear_subject_for_filename(subject[:150])}.eml"
                            email_filename_new = check_filename(imap_sub_folder, email_filename_new)

                            os.rename(email_filename, f"{imap_sub_folder}\\{email_filename_new}")
                            if os.path.exists(f"{imap_sub_folder}\\{email_filename_new}"):
                                delete_last_lines(1)
                                print(f"Rename to Subject.eml: {email_filename_new.replace(output, '')}")
                            # Set Timestamp

                            # date_obj = parser.parse(msg_date)

                            # creation_time = date_obj.timestamp()
                            # os.utime(f"{imap_sub_folder}\\{email_filename_new}", (creation_time, creation_time))
                            folder_total_size = folder_total_size+file_size
                            logging.info(f"Downloaded {imap_sub_folder}\\{email_filename_new}  ({convert_size(file_size)}) - Sender: »{sender}« - Receiver: »{receiver}« - Subject »{subject}« - MD5: {file_md5}")
                            delete_last_lines(2)
                            continue
                        logging.info(f'{email_count_format} emails ({convert_size(folder_total_size)}) downloaded in the folder »{imap_sub_folder}«.\n\n')
                # time.sleep(0.8)
                delete_last_lines(1)
            except Exception as error:
                delete_last_lines(1)
                logging.error(f"Error when processing folder {decoded_folder}: {error}")
    except Exception as e:
        logging.error(f"Error when using imapclient: {e}")
    finally:
        total_email_download_format = '{:,.0f}'.format(total_email_download)
        logging.info(f"{total_email_download_format}  emails downloaded from the mailbox")
        print(f"{total_email_download_format} emails downloaded from the mailbox")
        lines += 1
        if total_email_download == total_email:
            print(f'the same number {total_email_format} of emails as was read in was also downloaded.')
            logging.info(f'the same number {total_email_format} of emails as was read in was also downloaded.')
        if total_email_download != total_email:
            print(f'There was a difference between the read {total_email_format} and downloaded {total_email_download_format} emails. Please try again.')
            logging.error(f'There was a difference between the read {total_email_format} and downloaded {total_email_download_format} emails. Please try again.')
        lines += 1
        endtime = time.time()
        duration = endtime - starttime
        durationstring = formatduration(duration)
        # show during
        print(f"Duration of processing: {durationstring}")
        logging.info(f"Duration of processing: {durationstring}")
        lines += 1
        # Wait until ends
        try:
            lines += 1
            input("Press enter to exit")
        except SyntaxError:
            pass
def print_directory_tree(root, logging_file, output):
    with open(logging_file, 'a', encoding='utf-8') as f:
        f.write(f"+ {root.replace(output, '')}\n")
        for item in os.listdir(root):
            path = os.path.join(root, item)
            if os.path.isdir(path):
                print_directory_tree(path, logging_file, output)
            # else:
            #    f.write(f"  - {item.replace(email_folder_path, '')}\n")
    return

def test_imap_credentials(imapurl, sslport, username, password):
    ssl_context = ssl.create_default_context()

    # don't check if certificate hostname doesn't match target hostname
    ssl_context.check_hostname = False

    # don't check if the certificate is trusted by a certificate authority
    ssl_context.verify_mode = ssl.CERT_NONE
    imap_server = imapclient.IMAPClient(host=imapurl, port=sslport, ssl_context=ssl_context)
    try:
        # Execute authentication
        imap_server.login(username, password)
        imaptestresult = 1
        imapTestTimestamp = time.time()
        return imaptestresult, imapTestTimestamp
    except Exception as e:
        imaptestresult = 0
        imapTestTimestamp = time.time()
        return imaptestresult, imapTestTimestamp

    finally:
        # Close Connenection to IMAP server
        imap_server.logout()

#########################
# global variables
#########################
lines = 16
thisprogram = sys.argv[0]
file_path = os.path.realpath(thisprogram)
thisprogram = os.path.basename(file_path)
programTitle = """
   ____                              _         ____                _    __   ___                       __                __           
  / __/ ___   ____ ___   ___   ___  (_) ____  / __/  __ _  ___ _  (_)  / /  / _ \ ___  _    __  ___   / / ___  ___ _ ___/ / ___   ____
 / _/  / _ \ / __// -_) / _ \ (_-< / / / __/ / _/   /  ' \/ _ `/ / /  / /  / // // _ \| |/|/ / / _ \ / / / _ \/ _ `// _  / / -_) / __/
/_/    \___//_/   \__/ /_//_//___//_/  \__/ /___/  /_/_/_/\_,_/ /_/  /_/  /____/ \___/|__,__/ /_//_//_/  \___/\_,_/ \_,_/  \__/ /_/V.0.4-beta   

(C) 2023 - BrunoFischerBerlin - MIT Licence
Github: https://github.com/BrunoFischerGermany/FED-ForensicEmailDownloader
Email: ForensicEmailDownloader@mail.de
"""

example1 = f"python3 {thisprogram} --username user@example.com --password mypassword --imapurl imap.example.com --output C:\\tmp --rangeend 2023-01-01"
example2 = f"python3 {thisprogram} -u user@example.com -p mypassword -i imap.example.com -o /tmp/mails --rangebegin 2022-01-01"

#############################
# Main Window
#############################
def main(output=None, username=None, password=None, imapurl=None, sslport=None, evidence=None, examiner=None, case=None, rangebegin=None, rangeend=None):

    global lines
    usernameTestTimestamp = None
    usernameTestResult = 0
    imapTestTimestamp = None
    imaptestresult = 0
    try:
        urllib.request.urlopen('https://www.google.com', timeout=5)
        print("√ Internet connection exists.\n")
    except:
        print("Internet connection does not exists. Please check the Internet connection. This program will now be closed.")
        try:
            input("Press enter to exit this program.")
        except SyntaxError:
            pass
        exit()
    # Check a credential.file (credential.pkl) exists in the same folder of this program
    pickle_file = "credential.pkl"
    if os.path.exists(pickle_file) and os.access(pickle_file, os.W_OK) and os.path.getsize(pickle_file) > 0:
        print('√ Credential File found.\n')
        with open(pickle_file, 'rb') as f:
            var_list = pickle.load(f)
        # username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend = var_list
        # username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend = var_list + (None,) * (10 - len(var_list))
        username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend = list(var_list) + [None] * (10 - len(var_list))
        if rangebegin is None:
            rangebegin = "-empty-value-"
        if rangeend is None:
            rangeend = "-empty-value-"
    # check Connection to IMAP Server. If False, then check IMAPUrl, SSLPort, username and password
    if args.username:
        username = args.username
    if not username:
        username = "-empty-value-"
    if args.password:
        password = args.password
    if not password:
        password = "-empty-value-"
    if args.imapurl:
        imapurl = args.imapurl
    if imapurl == "-empty-value-" and username != "-empty-value-":
        domain = validate_email(username)
        if domain is not False:
            usernameTestTimestamp = time.time()
            usernameTestResult = 1
            imapurl, sslport = get_imap_settings(domain)
        else:
            imapurl = "-empty-value-"
    if not imapurl:
        if username != "-empty-value-":
            domain = validate_email(username)
            if domain is not False:
                usernameTestTimestamp = time.time()
                usernameTestResult = 1
                imapurl, sslport = get_imap_settings(domain)
            else:
                imapurl = "-empty-value-"
        if username == "-empty-value-":
            imapurl = "-empty-value-"
    if args.sslport:
        sslport = args.sslport
    if not sslport:
        sslport = 993
    if args.output:
        output = args.output
    if not output:
        output = "./"
    if args.examiner:
        examiner = args.examiner
    if not examiner:
        examiner = "-empty-value-"
    if args.case:
        case = args.case
    if not case:
        case = "-empty-value-"
    if args.evidence:
        evidence = args.evidence
    if not evidence:
        evidence = "-empty-value-"
    if args.rangebegin:
        rangebegin = args.rangebegin
        begin_obj = parser.parse(rangebegin)
        rangebegin = begin_obj.strftime("%Y-%m-%d")
    if not rangebegin:
        rangebegin = "-empty-value-"
    if args.rangeend:
        rangeend = args.rangeend
        end_obj = parser.parse(rangeend)
        rangeend = end_obj.strftime("%Y-%m-%d")
    if not rangeend:
        rangeend = "-empty-value-"

    # Get System Data

    login_name = os.getlogin()
    system = platform.system()

    if system == "Windows":
        osSystem = "Windows"
        osVersion = get_windows_version()
    elif system == "Darwin":
        osSystem = "MacOS"
        osVersion = get_macos_version()
    elif system == "Linux":
        osSystem = "Linux"
        osVersion = get_linux_version()
    else:
        osSystem = "Other"
        osVersion = "Other"

    # Print out System data
    print(f'operating system: {osSystem}')
    print(f'operating system version: {osVersion}')
    print(f'computer user: {login_name}\n')
    print("Please fill out the empty values!\n")
    ########################################
    # Print out the defaults
    ########################################
    while True:
        if imapTestTimestamp is not None:
            imapTestString = "Result: " + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(imapTestTimestamp))
            if imaptestresult == 0:
                imapTestString += " failed "
            if imaptestresult == 1:
                imapTestString += " successful "
        else:
            imapTestString = "Result: Credential not tested yet"
        # Check the Output Path exists and is writeable, when not, then go to stand ./
        if not os.path.exists(output) and not os.access(output, os.W_OK):
            outputPathString = "Entered Output Path is not valid, Now is Standard ./"
            output = "./"
        else:
            outputPathString = "√ Output Path is valid and exists "
        if usernameTestTimestamp is not None:
            usernameTestString = "Result: " + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(imapTestTimestamp))
            if usernameTestResult == 0:
                usernameTestString += " This Value is not a valid email address "
            if usernameTestResult == 1:
                usernameTestString += " This Value is a valid email address "
        else:
            usernameTestString = " This value is not tested yet "
        message = ""
        print(f'[1] Username/Email-Address:\t\t{username}\t{usernameTestString}')
        print(f'[2] Password:\t\t\t\t{password}')
        print(f'[3] IMAP-Server:\t\t\t{imapurl}')
        print(f'[4] IMAP-Server-SSL-Port:\t\t{sslport}')
        print(f'[5] Output-Path:\t\t\t{output}\t{outputPathString}')
        print(f'[6] Examiner:\t\t\t\t{examiner}')
        print(f'[7] Case:\t\t\t\t{case}')
        print(f'[8] Evicence:\t\t\t\t{evidence}')
        print(f'[B] Time Range Begin:\t\t\t{rangebegin}')
        print(f'[E] Time Range End:\t\t\t{rangeend}')
        print(f'[C] clear credential file')
        print(f'[T] test imap credentials\t\t{imapTestString}')
        print(f'[S] save email data to output path')
        choose = input('Please select an option [1-8; B; E; C, S; T] (q to exit): ')
        # End loop when 'q' is entered
        if choose.upper() == 'Q':
            break
        if choose.upper() == '1':
            username = input("Please enter the username/email address: ")
            imaptestresult = 0
            domain = validate_email(username)
            if domain is not False:
                usernameTestTimestamp = time.time()
                usernameTestResult = 1
                imapurl, sslport = get_imap_settings(domain)
        if choose.upper() == '2':
            password = input("Please enter the password: ")
            imaptestresult = 0
        if choose.upper() == '3':
            imapurl = input("Please enter the URL to the IMAP server: ")
            imaptestresult = 0
        if choose.upper() == '4':
            sslport = input("Please specify the port to the IMAP server: ")
            imaptestresult = 0
        if choose.upper() == '5':
            output = input("Please enter the output path: ")
        if choose.upper() == '6':
            examiner = input("Please specify an Examiner: ")
        if choose.upper() == '7':
            case = input("Please specify the case number: ")
        if choose.upper() == '8':
            evidence = input("Please specify the evidence object number: ")
        if choose.upper() == 'B':
            rangebegin = input("Please specify the begin date of the time range (YYYY-MM-DD): ")
            if len(rangebegin) > 0 and rangebegin != " ":
                begin_obj = parser.parse(rangebegin)
                rangebegin = begin_obj.strftime("%Y-%m-%d")
            else:
                rangebegin = "-empty-value-"
        if choose.upper() == 'E':
            rangeend = input("Please specify the end date of the time range (YYYY-MM-DD): ")
            if len(rangeend) > 0 and rangeend != " ":
                end_obj = parser.parse(rangeend)
                rangeend = end_obj.strftime("%Y-%m-%d")
            else:
                rangeend = "-empty-value-"
        if rangebegin > rangeend:
            rangeend = "-empty-value-"
            message += "The End Range date must biger then the range begin range"
        if choose.upper() == "C":
            with open(pickle_file, "w") as f:
                f.write("")
            username = "-empty-value-"
            password = "-empty-value-"
            imapurl = "-empty-value-"
            sslport = 993
            output = "./"
            examiner = "-empty-value-"
            case = "-empty-value-"
            evidence = "-empty-value-"
            rangebegin = "-empty-value-"
            rangeend = "-empty-value-"
            message = "credential file reset"
        if choose.upper() == 'S':
            if imaptestresult == 0:
                choose = "T"
            else:
                message = "execute export"
                export_email(username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend, osSystem, osVersion, login_name)
        if choose.upper() == "T":
            # Check Credentials not empty!
            if username == "-empty-value-" or password == "-empty-value-" or imapurl == "-empty-value-":
                message = "Please check the IMAP-Credentials like Username, Password, IMAP-Server. This should not be empty!"
            else:
                # Test the Credentials
                domain = validate_email(username)
                if domain is not False:
                    usernameTestTimestamp = time.time()
                    usernameTestResult = 1
                imaptestresult, imapTestTimestamp = test_imap_credentials(imapurl, sslport, username, password)
                message = "IMAP-Credentials tested"
        if choose.upper() != "C" and choose.upper() != "S" and choose.upper() != "T":
            if choose.upper() == "1" or choose.upper() == "2" or choose.upper() == "3" or choose.upper() == "4" or choose.upper() == "5" or choose.upper() == "6" or choose.upper() == "7" or choose.upper() == "8" or choose.upper() == "B" or choose.upper() == "E":
                with open(pickle_file, 'wb') as f:
                    pickle.dump([username, password, imapurl, sslport, output, examiner, case, evidence, rangebegin, rangeend], f)
                    message = "update credential file"
            if len(message) > 0:
                delete_last_lines(1)

        delete_last_lines(lines)
        lines = 16
        if len(message) > 0:
            print(f'Message: {message}\n')
        else:
            print('Nothing changed\n')

if __name__ == '__main__':
    os.system('mode con: cols=180 lines=50')
    arg_parser = argparse.ArgumentParser(description={programTitle}, formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""Example:
{example1}
{example2}

""")
    print(f"""
{programTitle}

Example: 
{example1}
{example2}
""")
    arg_parser.add_argument('-i', '--imapurl', dest='imapurl', help='IMAP-URL', default=None)
    arg_parser.add_argument('-u', '--username', dest='username', help='username', default=None)
    arg_parser.add_argument('-p', '--password', dest='password', help='password', default=None)
    arg_parser.add_argument('-o', '--output', dest='output', help='output Path', default=None)
    arg_parser.add_argument('-s', '--sslport', dest='sslport', help='SSL-Port', default=None)
    arg_parser.add_argument('-x', '--examiner', dest='examiner', help='Examiner', default=None)
    arg_parser.add_argument('-c', '--case', dest='case', help='Case', default=None)
    arg_parser.add_argument('-e', '--evidence', dest='evidence', help='Evidencenumber', default=None)
    arg_parser.add_argument('--rangebegin', dest='rangebegin', help='Begin of the timerange', default=None)
    arg_parser.add_argument('--rangeend', dest='rangeend', help='End of the timerange', default=None)
    args = arg_parser.parse_args()

    main(**vars(args))
