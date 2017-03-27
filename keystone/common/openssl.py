# Copyright 2012 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#

import os
import subprocess  # nosec : see comments in the code below

from oslo_log import log

from keystone.common import utils
import keystone.conf

LOG = log.getLogger(__name__)
CONF = keystone.conf.CONF

PUBLIC_DIR_PERMS = 0o755        # -rwxr-xr-x
PRIVATE_DIR_PERMS = 0o750       # -rwxr-x---
PUBLIC_FILE_PERMS = 0o644       # -rw-r--r--
PRIVATE_FILE_PERMS = 0o640      # -rw-r-----


def file_exists(file_path):
    return os.path.exists(file_path)


class BaseCertificateConfigure(object):
    """Create a certificate signing environment.

    This is based on a config section and reasonable OpenSSL defaults.

    """

    def __init__(self, conf_obj, keystone_user,
                 keystone_group, rebuild, **kwargs):
        self.conf_dir = os.path.dirname(conf_obj.ca_certs)
        self.use_keystone_user = keystone_user
        self.use_keystone_group = keystone_group
        self.rebuild = rebuild
        self.ssl_config_file_name = os.path.join(self.conf_dir, "openssl.conf")
        self.request_file_name = os.path.join(self.conf_dir, "req.pem")
        self.ssl_dictionary = {'conf_dir': self.conf_dir,
                               'ca_cert': conf_obj.ca_certs,
                               'default_md': 'default',
                               'ssl_config': self.ssl_config_file_name,
                               'ca_private_key': conf_obj.ca_key,
                               'request_file': self.request_file_name,
                               'signing_key': conf_obj.keyfile,
                               'signing_cert': conf_obj.certfile,
                               'key_size': int(conf_obj.key_size),
                               'valid_days': int(conf_obj.valid_days),
                               'cert_subject': conf_obj.cert_subject}

        try:
            # OpenSSL 1.0 and newer support default_md = default,
            # older versions do not
            openssl_ver = subprocess.check_output(  # nosec : the arguments
                # are hardcoded and just check the openssl version
                ['openssl', 'version'])
            if b'OpenSSL 0.' in openssl_ver:
                self.ssl_dictionary['default_md'] = 'sha1'
        except subprocess.CalledProcessError:
            LOG.warning('Failed to invoke ``openssl version``, '
                        'assuming is v1.0 or newer')
        self.ssl_dictionary.update(kwargs)

    def exec_command(self, command):
        to_exec = [part % self.ssl_dictionary for part in command]
        LOG.info('Running command - %s', ' '.join(to_exec))
        try:
            # NOTE(shaleh): use check_output instead of the simpler
            # `check_call()` in order to log any output from an error.
            subprocess.check_output(  # nosec : the arguments being passed
                # in are defined in this file and trusted to build CAs, keys
                # and certs
                to_exec,
                stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            msg = ("Command %(to_exec)s exited with %(retcode)s - "
                   "%(output)s)")
            LOG.error(msg,
                      {'to_exec': to_exec,
                       'retcode': e.returncode,
                       'output': e.output})
            raise

    def clean_up_existing_files(self):
        files_to_clean = [self.ssl_dictionary['ca_private_key'],
                          self.ssl_dictionary['ca_cert'],
                          self.ssl_dictionary['signing_key'],
                          self.ssl_dictionary['signing_cert'],
                          ]

        existing_files = []

        for file_path in files_to_clean:
            if file_exists(file_path):
                if self.rebuild:
                    # The file exists but the user wants to rebuild it, so blow
                    # it away
                    try:
                        os.remove(file_path)
                    except OSError as exc:
                        msg = ("Failed to remove file %(file_path)r: "
                               "%(error)s")
                        LOG.error(msg,
                                  {'file_path': file_path,
                                   'error': exc.strerror})
                        raise
                else:
                    existing_files.append(file_path)

        return existing_files

    def build_ssl_config_file(self):
        utils.make_dirs(os.path.dirname(self.ssl_config_file_name),
                        mode=PUBLIC_DIR_PERMS,
                        user=self.use_keystone_user,
                        group=self.use_keystone_group, log=LOG)
        if not file_exists(self.ssl_config_file_name):
            with open(self.ssl_config_file_name, 'w') as ssl_config_file:
                ssl_config_file.write(self.sslconfig % self.ssl_dictionary)
        utils.set_permissions(self.ssl_config_file_name,
                              mode=PRIVATE_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

        index_file_name = os.path.join(self.conf_dir, 'index.txt')
        if not file_exists(index_file_name):
            with open(index_file_name, 'w') as index_file:
                index_file.write('')
        utils.set_permissions(index_file_name,
                              mode=PRIVATE_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

        serial_file_name = os.path.join(self.conf_dir, 'serial')
        if not file_exists(serial_file_name):
            with open(serial_file_name, 'w') as index_file:
                index_file.write('01')
        utils.set_permissions(serial_file_name,
                              mode=PRIVATE_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

    def build_ca_cert(self):
        ca_key_file = self.ssl_dictionary['ca_private_key']
        utils.make_dirs(os.path.dirname(ca_key_file),
                        mode=PRIVATE_DIR_PERMS,
                        user=self.use_keystone_user,
                        group=self.use_keystone_group, log=LOG)
        if not file_exists(ca_key_file):
            self.exec_command(['openssl', 'genrsa',
                               '-out', '%(ca_private_key)s',
                               '%(key_size)d'])
        utils.set_permissions(ca_key_file,
                              mode=PRIVATE_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

        ca_cert = self.ssl_dictionary['ca_cert']
        utils.make_dirs(os.path.dirname(ca_cert),
                        mode=PUBLIC_DIR_PERMS,
                        user=self.use_keystone_user,
                        group=self.use_keystone_group, log=LOG)
        if not file_exists(ca_cert):
            self.exec_command(['openssl', 'req', '-new', '-x509',
                               '-extensions', 'v3_ca',
                               '-key', '%(ca_private_key)s',
                               '-out', '%(ca_cert)s',
                               '-days', '%(valid_days)d',
                               '-config', '%(ssl_config)s',
                               '-subj', '%(cert_subject)s'])
        utils.set_permissions(ca_cert,
                              mode=PUBLIC_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

    def build_private_key(self):
        signing_keyfile = self.ssl_dictionary['signing_key']
        utils.make_dirs(os.path.dirname(signing_keyfile),
                        mode=PRIVATE_DIR_PERMS,
                        user=self.use_keystone_user,
                        group=self.use_keystone_group, log=LOG)
        if not file_exists(signing_keyfile):
            self.exec_command(['openssl', 'genrsa', '-out', '%(signing_key)s',
                               '%(key_size)d'])
        utils.set_permissions(signing_keyfile,
                              mode=PRIVATE_FILE_PERMS,
                              user=self.use_keystone_user,
                              group=self.use_keystone_group, log=LOG)

    def build_signing_cert(self):
        signing_cert = self.ssl_dictionary['signing_cert']

        utils.make_dirs(os.path.dirname(signing_cert),
                        mode=PUBLIC_DIR_PERMS,
                        user=self.use_keystone_user,
                        group=self.use_keystone_group, log=LOG)
        if not file_exists(signing_cert):
            self.exec_command(['openssl', 'req', '-key', '%(signing_key)s',
                               '-new', '-out', '%(request_file)s',
                               '-config', '%(ssl_config)s',
                               '-subj', '%(cert_subject)s'])

            self.exec_command(['openssl', 'ca', '-batch',
                               '-out', '%(signing_cert)s',
                               '-config', '%(ssl_config)s',
                               '-days', '%(valid_days)d',
                               '-cert', '%(ca_cert)s',
                               '-keyfile', '%(ca_private_key)s',
                               '-infiles', '%(request_file)s'])

    def run(self):
        try:
            existing_files = self.clean_up_existing_files()
        except OSError:
            print('An error occurred when rebuilding cert files.')
            return
        if existing_files:
            print('The following cert files already exist, use --rebuild to '
                  'remove the existing files before regenerating:')
            for f in existing_files:
                print('%s already exists' % f)
            return

        self.build_ssl_config_file()
        self.build_ca_cert()
        self.build_private_key()
        self.build_signing_cert()


class ConfigurePKI(BaseCertificateConfigure):
    """Generate files for PKI signing using OpenSSL.

    Signed tokens require a private key and signing certificate which itself
    must be signed by a CA.  This class generates them with workable defaults
    if each of the files are not present

    """

    def __init__(self, keystone_user, keystone_group, rebuild=False):
        super(ConfigurePKI, self).__init__(CONF.signing, keystone_user,
                                           keystone_group, rebuild=rebuild)


BaseCertificateConfigure.sslconfig = """
# OpenSSL configuration file.
#

# Establish working directory.

dir            = %(conf_dir)s

[ ca ]
default_ca        = CA_default

[ CA_default ]
new_certs_dir     = $dir
serial            = $dir/serial
database          = $dir/index.txt
default_days      = 365
default_md        = %(default_md)s
preserve          = no
email_in_dn       = no
nameopt           = default_ca
certopt           = default_ca
policy            = policy_anything
x509_extensions   = usr_cert
unique_subject    = no

[ policy_anything ]
countryName             = optional
stateOrProvinceName     = optional
organizationName        = optional
organizationalUnitName  = optional
commonName              = supplied
emailAddress            = optional

[ req ]
default_bits       = 2048 # Size of keys
default_keyfile    = key.pem # name of generated keys
string_mask        = utf8only # permitted characters
distinguished_name = req_distinguished_name
req_extensions     = v3_req
x509_extensions = v3_ca

[ req_distinguished_name ]
countryName                 = Country Name (2 letter code)
countryName_min             = 2
countryName_max             = 2
stateOrProvinceName         = State or Province Name (full name)
localityName                = Locality Name (city, district)
0.organizationName          = Organization Name (company)
organizationalUnitName      = Organizational Unit Name (department, division)
commonName                  = Common Name (hostname, IP, or your name)
commonName_max              = 64
emailAddress                = Email Address
emailAddress_max            = 64

[ v3_ca ]
basicConstraints       = CA:TRUE
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always,issuer

[ v3_req ]
basicConstraints     = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment

[ usr_cert ]
basicConstraints       = CA:FALSE
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always
"""
