import json
import os
import shutil
import urlparse

from django.conf import settings
from django.core.cache import cache
from django.utils.encoding import iri_to_uri
from django.utils.http import http_date, urlencode

from mock import Mock, patch
from nose.tools import eq_
from pyquery import PyQuery as pq
import waffle
from waffle.models import Switch

import amo
import amo.tests
from amo.utils import Message
from amo.urlresolvers import reverse
from addons.models import Addon
from files.helpers import FileViewer, DiffHelper
from files.models import File
from market.models import AddonPurchase
from users.models import UserProfile

dictionary = 'apps/files/fixtures/files/dictionary-test.xpi'
unicode_filenames = 'apps/files/fixtures/files/unicode-filenames.xpi'
not_binary = 'install.js'
binary = 'dictionaries/ar.dic'


class FilesBase:

    def login_as_editor(self):
        assert self.client.login(username='editor@mozilla.com',
                                 password='password')

    def setUp(self):
        self.addon = Addon.objects.get(pk=3615)
        self.dev = self.addon.authors.all()[0]
        self.regular = UserProfile.objects.get(pk=999)
        self.version = self.addon.versions.latest()
        self.file = self.version.all_files[0]

        self.file_two = File()
        self.file_two.version = self.version
        self.file_two.filename = 'dictionary-test.xpi'
        self.file_two.save()

        self.login_as_editor()

        for file_obj in [self.file, self.file_two]:
            src = os.path.join(settings.ROOT, dictionary)
            try:
                os.makedirs(os.path.dirname(file_obj.file_path))
            except OSError:
                pass
            shutil.copyfile(src, file_obj.file_path)

        self.file_viewer = FileViewer(self.file)
        # Setting this to True, so we are delaying the extraction of files,
        # in the tests, the files won't be extracted.
        # Most of these tests extract as needed to.
        Switch.objects.get_or_create(name='delay-file-viewer', active=True)

    def tearDown(self):
        self.file_viewer.cleanup()

    def files_redirect(self, file):
        return reverse('files.redirect', args=[self.file.pk, file])

    def files_serve(self, file):
        return reverse('files.serve', args=[self.file.pk, file])

    def test_view_access_anon(self):
        self.client.logout()
        self.check_urls(403)

    def test_view_access_anon_view_source(self):
        self.addon.update(view_source=True)
        self.file_viewer.extract()
        self.client.logout()
        self.check_urls(200)

    def test_view_access_editor(self):
        self.file_viewer.extract()
        self.check_urls(200)

    def test_view_access_editor_view_source(self):
        self.addon.update(view_source=True)
        self.file_viewer.extract()
        self.check_urls(200)

    def test_view_access_developer(self):
        self.client.logout()
        assert self.client.login(username=self.dev.email, password='password')
        self.file_viewer.extract()
        self.check_urls(200)

    def test_view_access_reviewed(self):
        self.addon.update(view_source=True)
        self.file_viewer.extract()
        self.client.logout()

        for status in amo.UNREVIEWED_STATUSES:
            self.addon.update(status=status)
            self.check_urls(403)

        for status in amo.REVIEWED_STATUSES:
            self.addon.update(status=status)
            self.check_urls(200)

    def test_view_access_developer_view_source(self):
        self.client.logout()
        assert self.client.login(username=self.dev.email, password='password')
        self.addon.update(view_source=True)
        self.file_viewer.extract()
        self.check_urls(200)

    def test_view_access_another_developer(self):
        self.client.logout()
        assert self.client.login(username=self.regular.email,
                                 password='password')
        self.file_viewer.extract()
        self.check_urls(403)

    def test_view_access_another_developer_view_source(self):
        self.client.logout()
        assert self.client.login(username=self.regular.email,
                                 password='password')
        self.addon.update(view_source=True)
        self.file_viewer.extract()
        self.check_urls(200)

    def test_poll_extracted(self):
        self.file_viewer.extract()
        res = self.client.get(self.poll_url())
        eq_(res.status_code, 200)
        eq_(json.loads(res.content)['status'], True)

    def test_poll_not_extracted(self):
        res = self.client.get(self.poll_url())
        eq_(res.status_code, 200)
        eq_(json.loads(res.content)['status'], False)

    def test_poll_extracted_anon(self):
        self.client.logout()
        res = self.client.get(self.poll_url())
        eq_(res.status_code, 403)

    def test_content_headers(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url('install.js'))
        assert 'etag' in res._headers
        assert 'last-modified' in res._headers

    def test_content_headers_etag(self):
        self.file_viewer.extract()
        self.file_viewer.select('install.js')
        obj = getattr(self.file_viewer, 'left', self.file_viewer)
        etag = obj.selected.get('md5')
        res = self.client.get(self.file_url('install.js'),
                              HTTP_IF_NONE_MATCH=etag)
        eq_(res.status_code, 304)

    def test_content_headers_if_modified(self):
        self.file_viewer.extract()
        self.file_viewer.select('install.js')
        obj = getattr(self.file_viewer, 'left', self.file_viewer)
        date = http_date(obj.selected.get('modified'))
        res = self.client.get(self.file_url('install.js'),
                              HTTP_IF_MODIFIED_SINCE=date)
        eq_(res.status_code, 304)

    def test_file_header(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        url = res.context['file_link']['url']
        eq_(url, reverse('editors.review', args=[self.addon.slug]))

    def test_file_header_anon(self):
        self.client.logout()
        self.file_viewer.extract()
        self.addon.update(view_source=True)
        res = self.client.get(self.file_url(not_binary))
        url = res.context['file_link']['url']
        eq_(url, reverse('addons.detail', args=[self.addon.pk]))

    def test_content_no_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url())
        doc = pq(res.content)
        eq_(len(doc('#content')), 0)

    def test_no_files(self):
        res = self.client.get(self.file_url())
        eq_(res.status_code, 200)
        assert 'files' not in res.context

    @patch('waffle.switch_is_active')
    def test_no_files_switch(self, switch_is_active):
        switch_is_active.return_value = False
        # By setting the switch to False, we are not delaying the file
        # extraction. The files will be extracted and there will be
        # files in context.
        res = self.client.get(self.file_url())
        eq_(res.status_code, 200)
        assert 'files' in res.context

    def test_files(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url())
        eq_(res.status_code, 200)
        assert 'files' in res.context

    def test_files_anon(self):
        self.client.logout()
        res = self.client.get(self.file_url())
        eq_(res.status_code, 403)

    def test_files_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        eq_(res.status_code, 200)
        assert 'selected' in res.context

    def test_files_back_link(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        eq_(doc('#commands td:last').text(), 'Back to review')

    def test_files_back_link_anon(self):
        self.file_viewer.extract()
        self.client.logout()
        self.addon.update(view_source=True)
        res = self.client.get(self.file_url(not_binary))
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#commands td:last').text(), 'Back to addon')


class TestFileViewer(FilesBase, amo.tests.TestCase):
    fixtures = ['base/addon_3615', 'base/users']

    def poll_url(self):
        return reverse('files.poll', args=[self.file.pk])

    def file_url(self, file=None):
        args = [self.file.pk]
        if file:
            args.extend(['file', file])
        return reverse('files.list', args=args)

    def check_urls(self, status):
        for url in [self.poll_url(), self.file_url()]:
            eq_(self.client.get(url).status_code, status)

    def add_file(self, name, contents):
        dest = os.path.join(self.file_viewer.dest, name)
        open(dest, 'w').write(contents)

    def test_files_xss(self):
        self.file_viewer.extract()
        self.add_file('<script>alert("foo")', '')
        res = self.client.get(self.file_url())
        doc = pq(res.content)
        # Note: this is text, not a DOM element, so escaped correctly.
        assert '<script>alert("' in doc('#files li a').text()

    def test_content_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url('install.js'))
        doc = pq(res.content)
        eq_(len(doc('#content')), 1)

    def test_content_no_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url())
        doc = pq(res.content)
        eq_(len(doc('#content')), 1)
        eq_(res.context['key'], 'install.rdf')

    def test_content_xss(self):
        self.file_viewer.extract()
        for name in ['file.txt', 'file.html', 'file.htm']:
            # If you are adding files, you need to clear out the memcache
            # file listing.
            cache.clear()
            self.add_file(name, '<script>alert("foo")</script>')
            res = self.client.get(self.file_url(name))
            doc = pq(res.content)
            # Note: this is text, not a DOM element, so escaped correctly.
            assert doc('#content').text().startswith('<script')

    def test_binary(self):
        self.file_viewer.extract()
        self.add_file('file.php', '<script>alert("foo")</script>')
        res = self.client.get(self.file_url('file.php'))
        eq_(res.status_code, 200)
        assert self.file_viewer.get_files()['file.php']['md5'] in res.content

    def test_tree_no_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url('doesnotexist.js'))
        eq_(res.status_code, 404)

    def test_directory(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url('doesnotexist.js'))
        eq_(res.status_code, 404)

    def test_unicode(self):
        self.file_viewer.src = unicode_filenames
        self.file_viewer.extract()
        res = self.client.get(self.file_url(iri_to_uri(u'\u1109\u1161\u11a9')))
        eq_(res.status_code, 200)

    def test_serve_no_token(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_serve(binary))
        eq_(res.status_code, 403)

    def test_serve_fake_token(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_serve(binary) + '?token=aasd')
        eq_(res.status_code, 403)

    def test_serve_bad_token(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_serve(binary) + '?token=a asd')
        eq_(res.status_code, 403)

    def test_serve_get_token(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_redirect(binary))
        eq_(res.status_code, 302)
        url = res['Location']
        assert url.startswith(settings.STATIC_URL)
        assert urlparse.urlparse(url).query.startswith('token=')

    def test_memcache_goes_bye_bye(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_redirect(binary))
        url = res['Location'][len(settings.STATIC_URL):]
        cache.clear()
        res = self.client.get(url)
        eq_(res.status_code, 403)

    def test_bounce(self):
        self.file_viewer.extract()
        res = self.client.get(self.files_redirect(binary), follow=True)
        eq_(res.status_code, 200)
        eq_(res['X-SENDFILE'],
            self.file_viewer.get_files().get(binary)['full'])

    @patch.object(settings, 'FILE_VIEWER_SIZE_LIMIT', 5)
    def test_file_size(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        assert doc('.error').text().startswith('File size is')

    def test_poll_failed(self):
        msg = Message('file-viewer:%s' % self.file_viewer)
        msg.save('I like cheese.')
        res = self.client.get(self.poll_url())
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        eq_(data['status'], False)
        eq_(data['msg'], ['I like cheese.'])


class TestDiffViewer(FilesBase, amo.tests.TestCase):
    fixtures = ['base/addon_3615', 'base/users']

    def setUp(self):
        super(TestDiffViewer, self).setUp()
        self.file_viewer = DiffHelper(self.file, self.file_two)

    def poll_url(self):
        return reverse('files.compare.poll', args=[self.file.pk,
                                                   self.file_two.pk])

    def add_file(self, file_obj, name, contents):
        dest = os.path.join(file_obj.dest, name)
        open(dest, 'w').write(contents)

    def file_url(self, file=None):
        args = [self.file.pk, self.file_two.pk]
        if file:
            args.extend(['file', file])
        return reverse('files.compare', args=args)

    def check_urls(self, status):
        for url in [self.poll_url(), self.file_url()]:
            eq_(self.client.get(url).status_code, status)

    def test_tree_no_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url('doesnotexist.js'))
        eq_(res.status_code, 404)

    def test_content_file(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        eq_(len(doc('pre')), 3)

    def test_binary_serve_links(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(binary))
        doc = pq(res.content)
        node = doc('#content-wrapper a')
        eq_(len(node), 2)
        assert node[0].text.startswith('Download ar.dic')

    def test_view_both_present(self):
        self.file_viewer.extract()
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        eq_(len(doc('pre')), 3)
        eq_(len(doc('#content-wrapper p')), 2)

    def test_view_one_missing(self):
        self.file_viewer.extract()
        os.remove(os.path.join(self.file_viewer.right.dest, 'install.js'))
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        eq_(len(doc('pre')), 3)
        eq_(len(doc('#content-wrapper p')), 1)

    def test_view_left_binary(self):
        self.file_viewer.extract()
        filename = os.path.join(self.file_viewer.left.dest, 'install.js')
        open(filename, 'w').write('MZ')
        res = self.client.get(self.file_url(not_binary))
        assert 'This file is not viewable online' in res.content

    def test_view_right_binary(self):
        self.file_viewer.extract()
        filename = os.path.join(self.file_viewer.right.dest, 'install.js')
        open(filename, 'w').write('MZ')
        assert not self.file_viewer.is_diffable()
        res = self.client.get(self.file_url(not_binary))
        assert 'This file is not viewable online' in res.content

    def test_different_tree(self):
        self.file_viewer.extract()
        os.remove(os.path.join(self.file_viewer.left.dest, not_binary))
        res = self.client.get(self.file_url(not_binary))
        doc = pq(res.content)
        eq_(doc('h4:last').text(), 'Deleted files:')
        eq_(len(doc('ul.root')), 2)


class TestBuilderPingback(amo.tests.TestCase):

    def post(self, data):
        return self.client.post(reverse('amo.builder-pingback'), data)

    @patch('files.tasks.repackage_jetpack')
    def test_success(self, repackage_jetpack):
        repackage_jetpack.delay = Mock()
        r = self.post({'result': '', 'msg': '', 'filename': '',
                       'location': '', 'request': '',
                       'secret': settings.BUILDER_SECRET_KEY})
        assert repackage_jetpack.called
        eq_(r.status_code, 200)

    def test_bad_secret(self):
        r = self.post({'secret': 1})
        eq_(r.status_code, 400)

    def test_bad_data(self):
        r = self.post({'wut': 0})
        eq_(r.status_code, 400)


@patch.object(waffle, 'switch_is_active', lambda x: True)
class TestWatermarkedFile(amo.tests.TestCase, amo.tests.AMOPaths):
    fixtures = ['base/addon_3615', 'base/users.json']

    def setUp(self):
        self.addon = Addon.objects.get(pk=3615)
        self.addon.update(premium_type=amo.ADDON_PREMIUM)
        self.file = self.addon.current_version.all_files[0]
        self.xpi_copy_over(self.file, 'firefm')
        self.url = reverse('downloads.watermarked', args=[self.file.pk])
        self.user = UserProfile.objects.get(pk=999)
        self.author = self.addon.authors.all()[0]
        self.purchase = AddonPurchase.objects.create(addon=self.addon,
                                                     user=self.user)
        self.client.login(username='regular@mozilla.com', password='password')

    def get_anon(self, user=None, hsh=None):
        self.client.logout()
        url = reverse('downloads.watermarked', args=[self.file.pk])
        qs = urlencode({amo.WATERMARK_KEY: user,
                        amo.WATERMARK_KEY_HASH: hsh})
        return self.client.get('%s?%s' % (url, qs))

    def test_get_anon_watermarked(self):
        eq_(self.get_anon().status_code, 403)

    def test_get_anon_email(self):
        eq_(self.get_anon(user=self.user.email).status_code, 403)

    def test_get_anon_hash(self):
        eq_(self.get_anon(user=self.user.email, hsh='123').status_code, 403)

    def test_get_good_hash(self):
        data = {'user': self.user.email,
                'hsh': self.addon.get_watermark_hash(self.user)}
        eq_(self.get_anon(**data).status_code, 200)

    def test_good_hash_for_free(self):
        self.purchase.delete()
        data = {'user': self.user.email,
                'hsh': self.addon.get_watermark_hash(self.user)}
        eq_(self.get_anon(**data).status_code, 403)

    def test_get_watermarked(self):
        res = self.client.get(self.url)
        assert os.path.exists(res['X-SENDFILE'])

    def test_get_headers(self):
        res = self.client.get(self.url)
        eq_(res['Content-Type'], 'application/xp-install')
        eq_(res['Cache-Control'], 'max-age=0')

    def test_get_disabled(self):
        self.addon.update(status=amo.STATUS_DISABLED)
        res = self.client.get(self.url)
        eq_(res.status_code, 404)

    def test_get_free(self):
        self.addon.update(premium_type=amo.ADDON_FREE)
        res = self.client.get(self.url)
        eq_(res.status_code, 404)

    def test_watermark_locked(self):
        dest = self.file.watermarked_file_path(self.user.pk)
        msg = Message('marketplace.watermark.%s' % dest)
        msg.save(True)
        res = self.client.get(self.url)
        eq_(res.status_code, 404)

    def test_not_purchased(self):
        self.purchase.delete()
        res = self.client.get(self.url)
        eq_(res.status_code, 403)

    def test_watermark_latest_redirects(self):
        url = reverse('downloads.latest', args=[self.addon.slug])
        res = self.client.get(url, follow=False)
        self.assertRedirects(res, '%s/%s' % (self.url, self.file.filename))

    def test_author_can_get(self):
        self.client.logout()
        self.client.login(username=self.author.email, password='password')
        res = self.client.get(self.url)
        assert os.path.exists(res['X-SENDFILE'])
