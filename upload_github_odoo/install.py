from docutils import nodes
from docutils.core import publish_string
from docutils.transforms import Transform, writer_aux
from docutils.writers.html4css1 import Writer
import imp
import logging
import os
import re
import shutil
import tempfile
import urllib
import urllib2
import urlparse
import zipfile
import zipimport

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO   # NOQA

import openerp
import openerp.exceptions
from openerp import modules, pooler, tools, addons
from openerp.modules.db import create_categories
from openerp.tools.parse_version import parse_version
from openerp.tools.translate import _
from openerp.osv import fields, osv, orm

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

class ZipInstall(osv.osv):
    _name = "zip.install"

    name = fields.char()
    url = fields.char()
    is_valid = fields.boolean(default=False)

    
    def install_from_zip(self, cr, uid,ids, context=None):
        print('test')
        obj = self.pool.get('zip.install').browse(cr,uid,ids)

        url = obj.url
        print(url)
        ls = obj.url.split('/')
        module_name = ls[-3]+'-'+ls[-1].split('.')[0]
        if not self.pool['res.users'].has_group(cr, uid, 'base.group_system'):
            raise openerp.exceptions.AccessDenied()

        OPENERP = 'openerp'
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
                content = opener.open(url).read()
            except Exception:
                _logger.exception('Failed to fetch module %s', module_name)
                raise osv.except_osv(_('Module not found'),
                                         _('The `%s` module appears to be unavailable at the moment, please try again later.') % module_name)
               
            else:
                zipfile.ZipFile(StringIO(content)).extractall(tmp)
                assert os.path.isdir(os.path.join(tmp, module_name))

            # 2a. Copy/Replace module source in addons path
            
            module_path = modules.get_module_path(module_name, downloaded=True, display_warning=False)
            bck = backup(module_path, False)
            _logger.info('Copy downloaded module `%s` to `%s`', module_name, module_path)
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
                server_dir = openerp.tools.config['root_path']      # XXX or dirname()
                bck = backup(server_dir)
                _logger.info('Copy downloaded module `openerp` to `%s`', server_dir)
                shutil.move(os.path.join(tmp, OPENERP), server_dir)
                #if bck:
                #    shutil.rmtree(bck)

            self.pool['ir.module.module'].update_list(cr, uid, context=context)

            downloaded_ids = self.pool['ir.module.module'].search(cr, uid, [('name', '=', module_name)], context=context)
            already_installed = self.pool['ir.module.module'].search(cr, uid, [('id', 'in', downloaded_ids), ('state', '=', 'installed')], context=context)

            to_install_ids = self.pool['ir.module.module'].search(cr, uid, [('name', '=', module_name), ('state', '=', 'uninstalled')], context=context)
            post_install_action = self.pool['ir.module.module'].button_immediate_install(cr, uid, to_install_ids, context=context)

            if already_installed:
                # in this case, force server restart to reload python code...
                cr.commit()
                try :
                    openerp.service.restart_server()
                except:
                    openerp.service.server.restart()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'home',
                    'params': {'wait': True},
                }
            return post_install_action
        finally:
            shutil.rmtree(tmp)

    
    def verify_url(self,cr,uid,ids,url=None,context=None):
        
        name = False
        is_valid = False
        if url:
            if url.startswith('https://github.com/'):
                ls = url.split('/')
                name = ls[-3]+'-'+ls[-1].split('.')[0]
                is_valid = True
        return {'value':{'name':name,'is_valid':is_valid}}


    def install_from_zip_remote(self, cr, uid,vals, context=None):
        
        url = vals.get('url')
        ls = url.split('/')
        module_name = ls[-3]+'-'+ls[-1].split('.')[0]
        if not self.pool['res.users'].has_group(cr, uid, 'base.group_system'):
            raise openerp.exceptions.AccessDenied()

        OPENERP = 'openerp'
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
                content = opener.open(url).read()
            except Exception:
                _logger.exception('Failed to fetch module %s', module_name)
                raise osv.except_osv(_('Module not found'),
                                         _('The `%s` module appears to be unavailable at the moment, please try again later.') % module_name)
               
            else:
                zipfile.ZipFile(StringIO(content)).extractall(tmp)
                assert os.path.isdir(os.path.join(tmp, module_name))

            # 2a. Copy/Replace module source in addons path
            
            module_path = modules.get_module_path(module_name, downloaded=True, display_warning=False)
            bck = backup(module_path, False)
            _logger.info('Copy downloaded module `%s` to `%s`', module_name, module_path)
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
                server_dir = openerp.tools.config['root_path']      # XXX or dirname()
                bck = backup(server_dir)
                _logger.info('Copy downloaded module `openerp` to `%s`', server_dir)
                shutil.move(os.path.join(tmp, OPENERP), server_dir)
                #if bck:
                #    shutil.rmtree(bck)

            self.pool['ir.module.module'].update_list(cr, uid, context=context)

            downloaded_ids = self.pool['ir.module.module'].search(cr, uid, [('name', '=', module_name)], context=context)
            already_installed = self.pool['ir.module.module'].search(cr, uid, [('id', 'in', downloaded_ids), ('state', '=', 'installed')], context=context)

            to_install_ids = self.pool['ir.module.module'].search(cr, uid, [('name', '=', module_name), ('state', '=', 'uninstalled')], context=context)
            post_install_action = self.pool['ir.module.module'].button_immediate_install(cr, uid, to_install_ids, context=context)

            if already_installed:
                # in this case, force server restart to reload python code...
                cr.commit()
                try :
                    openerp.service.restart_server()
                Exception:
                    openerp.service.server.restart()
                    
                return {
                    'type': 'ir.actions.client',
                    'tag': 'home',
                    'params': {'wait': True},
                }
            return post_install_action
        finally:
            shutil.rmtree(tmp)