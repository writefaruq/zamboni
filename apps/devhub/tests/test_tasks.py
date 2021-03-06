import json
import os
import path
import shutil
import socket
import tempfile
import urllib2

from django.conf import settings

import mock
from nose.tools import eq_
from PIL import Image

import amo
import amo.tests
from addons.models import Addon
from amo.tests.test_helpers import get_image_path
from amo.urlresolvers import reverse
from amo.utils import ImageCheck
from devhub import tasks
from devhub.tests.test_views import BaseWebAppTest
from files.models import FileUpload


def test_resize_icon_shrink():
    """ Image should be shrunk so that the longest side is 32px. """

    resize_size = 32
    final_size = (32, 12)

    _uploader(resize_size, final_size)


def test_resize_icon_enlarge():
    """ Image stays the same, since the new size is bigger than both sides. """

    resize_size = 100
    final_size = (82, 31)

    _uploader(resize_size, final_size)


def test_resize_icon_same():
    """ Image stays the same, since the new size is the same. """

    resize_size = 82
    final_size = (82, 31)

    _uploader(resize_size, final_size)


def test_resize_icon_list():
    """ Resize multiple images at once. """

    resize_size = [32, 82, 100]
    final_size = [(32, 12), (82, 31), (82, 31)]

    _uploader(resize_size, final_size)


def _uploader(resize_size, final_size):
    img = get_image_path('mozilla.png')
    original_size = (82, 31)

    src = tempfile.NamedTemporaryFile(mode='r+w+b', suffix=".png",
                                      delete=False)

    # resize_icon removes the original
    shutil.copyfile(img, src.name)

    src_image = Image.open(src.name)
    eq_(src_image.size, original_size)

    if isinstance(final_size, list):
        for rsize, fsize in zip(resize_size, final_size):
            dest_name = str(path.path(settings.ADDON_ICONS_PATH) / '1234')

            tasks.resize_icon(src.name, dest_name, resize_size)
            dest_image = Image.open("%s-%s.png" % (dest_name, rsize))
            eq_(dest_image.size, fsize)

            if os.path.exists(dest_image.filename):
                os.remove(dest_image.filename)
            assert not os.path.exists(dest_image.filename)
    else:
        dest = tempfile.NamedTemporaryFile(mode='r+w+b', suffix=".png")
        tasks.resize_icon(src.name, dest.name, resize_size)
        dest_image = Image.open(dest.name)
        eq_(dest_image.size, final_size)

    assert not os.path.exists(src.name)


class TestValidator(amo.tests.TestCase):

    def setUp(self):
        self.upload = FileUpload.objects.create()
        assert not self.upload.valid

    def get_upload(self):
        return FileUpload.objects.get(pk=self.upload.pk)

    @mock.patch('devhub.tasks.run_validator')
    def test_pass_validation(self, _mock):
        _mock.return_value = '{"errors": 0}'
        tasks.validator(self.upload.pk)
        assert self.get_upload().valid

    @mock.patch('devhub.tasks.run_validator')
    def test_fail_validation(self, _mock):
        _mock.return_value = '{"errors": 2}'
        tasks.validator(self.upload.pk)
        assert not self.get_upload().valid

    @mock.patch('devhub.tasks.run_validator')
    def test_validation_error(self, _mock):
        _mock.side_effect = Exception
        eq_(self.upload.task_error, None)
        with self.assertRaises(Exception):
            tasks.validator(self.upload.pk)
        error = self.get_upload().task_error
        assert error.startswith('Traceback (most recent call last)'), error


class TestFlagBinary(amo.tests.TestCase):
    fixtures = ['base/addon_3615']

    def setUp(self):
        self.addon = Addon.objects.get(pk=3615)

    @mock.patch('devhub.tasks.run_validator')
    def test_flag_binary(self, _mock):
        _mock.return_value = '{"metadata":{"contains_binary_extension": 1}}'
        tasks.flag_binary([self.addon.pk])
        eq_(Addon.objects.get(pk=self.addon.pk).binary, True)

    @mock.patch('devhub.tasks.run_validator')
    def test_flag_not_binary(self, _mock):
        _mock.return_value = '{"metadata":{"contains_binary_extension": 0}}'
        tasks.flag_binary([self.addon.pk])
        eq_(Addon.objects.get(pk=self.addon.pk).binary, False)

    @mock.patch('devhub.tasks.run_validator')
    def test_flag_error(self, _mock):
        _mock.side_effect = RuntimeError()
        tasks.flag_binary([self.addon.pk])
        eq_(Addon.objects.get(pk=self.addon.pk).binary, False)


class TestFetchManifest(amo.tests.TestCase):

    def setUp(self):
        self.upload = FileUpload.objects.create()
        self.content_type = 'application/x-web-app-manifest+json'

        patcher = mock.patch('devhub.tasks.urllib2.urlopen')
        self.urlopen_mock = patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch('devhub.tasks.validator')
    def test_success_add_file(self, validator_mock):
        response_mock = mock.Mock()
        response_mock.read.return_value = 'woo'
        response_mock.headers = {'Content-Type': self.content_type}
        self.urlopen_mock.return_value = response_mock

        tasks.fetch_manifest('http://xx.com/manifest.json', self.upload.pk)
        upload = FileUpload.objects.get(pk=self.upload.pk)
        eq_(upload.name, 'http://xx.com/manifest.json')
        eq_(open(upload.path).read(), 'woo')

    @mock.patch('devhub.tasks.validator')
    def test_success_call_validator(self, validator_mock):
        response_mock = mock.Mock()
        response_mock.read.return_value = 'woo'
        ct = self.content_type + '; charset=utf-8'
        response_mock.headers = {'Content-Type': ct}
        self.urlopen_mock.return_value = response_mock

        tasks.fetch_manifest('http://xx.com/manifest.json', self.upload.pk)
        assert validator_mock.called

    def check_validation(self, msg):
        upload = FileUpload.objects.get(pk=self.upload.pk)
        validation = json.loads(upload.validation)
        eq_(validation['errors'], 1)
        eq_(validation['success'], False)
        eq_(len(validation['messages']), 1)
        eq_(validation['messages'][0]['message'], msg)

    def test_connection_error(self):
        reason = socket.gaierror(8, 'nodename nor servname provided')
        self.urlopen_mock.side_effect = urllib2.URLError(reason)
        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation('Could not contact host at "url".')

    def test_url_timeout(self):
        reason = socket.timeout('too slow')
        self.urlopen_mock.side_effect = urllib2.URLError(reason)
        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation('Connection to "url" timed out.')

    def test_other_url_error(self):
        reason = Exception('Some other failure.')
        self.urlopen_mock.side_effect = urllib2.URLError(reason)
        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation('Some other failure.')

    def test_no_content_type(self):
        response_mock = mock.Mock()
        response_mock.read.return_value = 'woo'
        response_mock.headers = {}
        self.urlopen_mock.return_value = response_mock

        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation(
            'Your manifest must be served with the HTTP header '
            '"Content-Type: application/x-web-app-manifest+json".')

    def test_bad_content_type(self):
        response_mock = mock.Mock()
        response_mock.read.return_value = 'woo'
        response_mock.headers = {'Content-Type': 'x'}
        self.urlopen_mock.return_value = response_mock

        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation(
            'Your manifest must be served with the HTTP header '
            '"Content-Type: application/x-web-app-manifest+json". We saw "x".')

    def test_response_too_large(self):
        response_mock = mock.Mock()
        content = 'x' * (settings.MAX_WEBAPP_UPLOAD_SIZE + 1)
        response_mock.read.return_value = content
        response_mock.headers = {'Content-Type': self.content_type}
        self.urlopen_mock.return_value = response_mock

        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation('Your manifest must be less than 2097152 bytes.')

    def test_http_error(self):
        self.urlopen_mock.side_effect = urllib2.HTTPError(
            'url', 404, 'Not Found', [], None)
        tasks.fetch_manifest('url', self.upload.pk)
        self.check_validation('url responded with 404 (Not Found).')


class TestFetchIcon(BaseWebAppTest):

    def setUp(self):
        super(TestFetchIcon, self).setUp()
        self.content_type = 'image/png'
        self.apps_path = (path.path(settings.ROOT) / 'apps'
                          / 'devhub' / 'tests' / 'addons')
        patcher = mock.patch('devhub.tasks.urllib2.urlopen')
        self.urlopen_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def webapp_from_path(self, path):
        self.upload = self.get_upload(abspath=path)
        self.url = reverse('devhub.submit.2')
        assert self.client.login(username='regular@mozilla.com',
                                 password='password')
        self.client.post(reverse('devhub.submit.1'))
        return self.post_addon()

    def test_no_icons(self):
        path = self.apps_path / 'noicon.webapp'
        iconless_app = self.webapp_from_path(path)
        tasks.fetch_icon(iconless_app)
        assert not self.urlopen_mock.called

    def check_icons(self, webapp):
        manifest = webapp.get_manifest_json()
        biggest = max([int(size) for size in manifest['icons']])

        icon_dir = webapp.get_icon_dir()
        for size in amo.ADDON_ICON_SIZES:
            if not size <= biggest:
                continue
            icon_path = os.path.join(icon_dir, '%s-%s.png'
                                     % (str(webapp.id), size))
            with open(icon_path, 'r') as img:
                checker = ImageCheck(img)
                assert checker.is_image()
                eq_(checker.img.size, (size, size))

    def test_data_uri(self):
        app_path = self.apps_path / 'dataicon.webapp'
        webapp = self.webapp_from_path(app_path)

        tasks.fetch_icon(webapp)
        eq_(webapp.icon_type, self.content_type)

        self.check_icons(webapp)

    def test_hosted_icon(self):
        app_path = self.apps_path / 'mozball.webapp'
        webapp = self.webapp_from_path(app_path)

        img_path = self.apps_path / 'mozball-128.png'
        with open(img_path, 'r') as content:
            tasks.save_icon(webapp, content.read())
        eq_(webapp.icon_type, self.content_type)

        self.check_icons(webapp)
