from collections import defaultdict
from operator import attrgetter
import importlib
import logging
import os
import shutil
import tempfile
import urllib2
import urlparse
import zipfile

from docutils import nodes
from docutils.core import publish_string
from docutils.transforms import Transform, writer_aux
from docutils.writers.html4css1 import Writer
import lxml.html

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO   # NOQA

import odoo
from odoo import api, fields, models, modules, tools, _
from odoo.exceptions import AccessDenied, UserError
from odoo.tools.parse_version import parse_version
    
_logger = logging.getLogger(__name__)

def backup(path, raise_exception=True):
    path = os.path.normpath(path)
    if not os.path.exists(path):
        if not raise_exception:
            return None
        raise OSError('path does not exists')
    cnt = 1
    while True:
        bck = '%s~%d' % (path, cnt)
        if not os.path.exists(bck):
            shutil.move(path, bck)
            return bck
        cnt += 1

class ZipInstall(models.Model):
    _name = "zip.install"

    name = fields.Char()
    url = fields.Char()
    is_valid = fields.Boolean(default=False)

    @api.one
    def install_from_zip(self):
        cr = self._cr
        url = self.url
        ls = self.url.split('/')
        module_name = ls[-3]+'-'+ls[-1].split('.')[0]
        print("name = "+module_name)
        if not self.env.user.has_group('base.group_system'):
            raise AccessDenied()

        apps_server = urlparse.urlparse(self.env['ir.module.module'].get_apps_server())

        OPENERP = odoo.release.product_name.lower()
        tmp = tempfile.mkdtemp()
        _logger.debug('Install from zip: %r', url)
        try:
            # 1. Download & unzip missing modules
            if not url:
                return False    # nothing to download, local version is already the last one
            try:
                _logger.info('Downloading module `%s` from github', module_name)
                opener = urllib2.build_opener()
                opener.addheader = [('User-agent','Mozilla/5.0')]
                print(module_name)
                content = opener.open(url).read()
            except Exception:
                _logger.exception('Failed to fetch module %s', module_name)
                raise UserError(_('The `%s` module appears to be unavailable at the moment, please try again later.') % module_name)
            else:
                zipfile.ZipFile(StringIO(content)).extractall(tmp)
                assert os.path.isdir(os.path.join(tmp, module_name))

            # 2a. Copy/Replace module source in addons path
            
            module_path = modules.get_module_path(module_name, downloaded=True, display_warning=False)
            bck = backup(module_path, False)
            _logger.info('Copy downloaded module `%s` to `%s`', module_name, module_path)
            print(str(os.path.join(tmp, module_name)))
            print(str(module_path))
            shutil.move(os.path.join(tmp, module_name), module_path)
            if bck:
                shutil.rmtree(bck)

            # 2b.  Copy/Replace server+base module source if downloaded
            if module_name == OPENERP:
                # special case. it contains the server and the base module.
                # extract path is not the same
                base_path = os.path.dirname(modules.get_module_path('base'))

                # copy all modules in the SERVER/openerp/addons directory to the new "openerp" module (except base itself)
                for d in os.listdir(base_path):
                    if d != 'base' and os.path.isdir(os.path.join(base_path, d)):
                        destdir = os.path.join(tmp, OPENERP, 'addons', d)    # XXX 'openerp' subdirectory ?
                        shutil.copytree(os.path.join(base_path, d), destdir)

                # then replace the server by the new "base" module
                server_dir = tools.config['root_path']      # XXX or dirname()
                bck = backup(server_dir)
                _logger.info('Copy downloaded module `openerp` to `%s`', server_dir)
                shutil.move(os.path.join(tmp, OPENERP), server_dir)
                #if bck:
                #    shutil.rmtree(bck)

            self.env['ir.module.module'].update_list()

            downloaded_ids = self.env['ir.module.module'].search([('name', '=', module_name)])
            already_installed = self.env['ir.module.module'].search([('id', 'in', [id for u.id in downloaded_ids]), ('state', '=', 'installed')])

            to_install_ids = self.env['ir.module.module'].search([('name', '=', module_name), ('state', '=', 'uninstalled')])
            post_install_action = self.env['ir.module.module'].button_immediate_install()

            if already_installed:
                # in this case, force server restart to reload python code...
                cr.commit()
                odoo.service.server.restart()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'home',
                    'params': {'wait': True},
                }
            return post_install_action
        finally:
            shutil.rmtree(tmp)

    @api.onchange('url')
    def verify_url(self):
        self.name = False
        self.is_valid = False
        if self.url:
            if self.url.startswith('https://github.com/'):
                ls = self.url.split('/')
                self.name = ls[-3]+'-'+ls[-1].split('.')[0]
                self.is_valid = True

    @api.model
    def install_from_zip_remote(self,vals):
        cr = self._cr
        url = vals.get('url')
        ls = url.split('/')
        module_name = ls[-3]+'-'+ls[-1].split('.')[0]
        print("name = "+module_name)
        if not self.env.user.has_group('base.group_system'):
            raise AccessDenied()

        apps_server = urlparse.urlparse(self.env['ir.module.module'].get_apps_server())

        OPENERP = odoo.release.product_name.lower()
        tmp = tempfile.mkdtemp()
        _logger.debug('Install from zip: %r', url)
        try:
            # 1. Download & unzip missing modules
            if not url:
                return False    # nothing to download, local version is already the last one
            try:
                _logger.info('Downloading module `%s` from github', module_name)
                opener = urllib2.build_opener()
                opener.addheader = [('User-agent','Mozilla/5.0')]
                print(module_name)
                content = opener.open(url).read()
            except Exception:
                _logger.exception('Failed to fetch module %s', module_name)
                raise UserError(_('The `%s` module appears to be unavailable at the moment, please try again later.') % module_name)
            else:
                zipfile.ZipFile(StringIO(content)).extractall(tmp)
                assert os.path.isdir(os.path.join(tmp, module_name))

            # 2a. Copy/Replace module source in addons path
            
            module_path = modules.get_module_path(module_name, downloaded=True, display_warning=False)
            bck = backup(module_path, False)
            _logger.info('Copy downloaded module `%s` to `%s`', module_name, module_path)
            print(str(os.path.join(tmp, module_name)))
            print(str(module_path))
            shutil.move(os.path.join(tmp, module_name), module_path)
            if bck:
                shutil.rmtree(bck)

            # 2b.  Copy/Replace server+base module source if downloaded
            if module_name == OPENERP:
                # special case. it contains the server and the base module.
                # extract path is not the same
                base_path = os.path.dirname(modules.get_module_path('base'))

                # copy all modules in the SERVER/openerp/addons directory to the new "openerp" module (except base itself)
                for d in os.listdir(base_path):
                    if d != 'base' and os.path.isdir(os.path.join(base_path, d)):
                        destdir = os.path.join(tmp, OPENERP, 'addons', d)    # XXX 'openerp' subdirectory ?
                        shutil.copytree(os.path.join(base_path, d), destdir)

                # then replace the server by the new "base" module
                server_dir = tools.config['root_path']      # XXX or dirname()
                bck = backup(server_dir)
                _logger.info('Copy downloaded module `openerp` to `%s`', server_dir)
                shutil.move(os.path.join(tmp, OPENERP), server_dir)
                #if bck:
                #    shutil.rmtree(bck)

            self.env['ir.module.module'].update_list()

            downloaded_ids = self.env['ir.module.module'].search([('name', '=', module_name)])
            already_installed = self.env['ir.module.module'].search([('id', 'in', [id for u.id in downloaded_ids]), ('state', '=', 'installed')])

            to_install_ids = self.env['ir.module.module'].search([('name', '=', module_name), ('state', '=', 'uninstalled')])
            post_install_action = self.env['ir.module.module'].button_immediate_install()

            if already_installed:
                # in this case, force server restart to reload python code...
                cr.commit()
                odoo.service.server.restart()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'home',
                    'params': {'wait': True},
                }
            return post_install_action
        finally:
            shutil.rmtree(tmp)