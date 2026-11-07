import unittest
from unittest.mock import Mock, patch
from uploads import upload_and_share, server_url, UploadError
from desktop import validated


class UploadTests(unittest.TestCase):
    def setUp(self):
        from uploads import short_slug
        self.original_slug = short_slug
        patcher = patch('uploads.short_slug', return_value='abc123')
        self.slug = patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, *responses):
        session = Mock()
        session.request.side_effect = responses
        return session

    def response(self, status, body=None, text=''):
        if isinstance(body, dict) and 'key' in body and 'slug' not in body:
            body = {**body, 'slug': 'abc123'}
        result = Mock(status_code=status, text=text)
        result.json.return_value = body
        return result

    def test_uploaded_bytes_create_share_and_key_is_not_in_link(self):
        session = self.session(self.response(201, {'id':'asset-id'}), self.response(201, {'key':'share/key+'}))
        link = upload_and_share(b'selected-png', 'https://photos.example.com/api/', 'private-api-key', 7, session)
        self.assertEqual(link, 'https://photos.example.com/s/abc123')
        upload, share = session.request.call_args_list
        self.assertEqual(upload.args, ('POST', 'https://photos.example.com/api/assets'))
        self.assertEqual(upload.kwargs['files']['assetData'][1], b'selected-png')
        self.assertEqual(upload.kwargs['headers'], {'x-api-key':'private-api-key'})
        self.assertFalse(upload.kwargs['allow_redirects'])
        self.assertEqual(upload.kwargs['data']['visibility'], 'archive')
        self.assertEqual(share.kwargs['json']['assetIds'], ['asset-id'])
        self.assertFalse(share.kwargs['json']['allowUpload'])
        self.assertFalse(share.kwargs['json']['showMetadata'])
        self.assertIn('expiresAt', share.kwargs['json'])
        self.assertNotIn('private-api-key', link)

    def test_legacy_device_fields_are_retried_only_for_matching_validation_error(self):
        session = self.session(self.response(400, text='deviceAssetId must be a string'),
                               self.response(201, {'id':'legacy'}), self.response(201, {'key':'abc'}))
        self.assertEqual(upload_and_share(b'png', 'photos.example.com', 'key', session=session),
                         'https://photos.example.com/s/abc123')
        self.assertEqual(session.request.call_count, 3)
        self.assertIn('deviceAssetId', session.request.call_args_list[1].kwargs['data'])

    def test_duplicate_asset_can_be_shared(self):
        session = self.session(self.response(200, {'id':'existing', 'status':'duplicate'}),
                               self.response(200, {'visibility':'archive'}),
                               self.response(201, {'key':'abc'}))
        upload_and_share(b'png', 'photos.example.com', 'key', session=session)
        archive = session.request.call_args_list[1]
        self.assertEqual(archive.args, ('PUT', 'https://photos.example.com/api/assets/existing'))
        self.assertEqual(archive.kwargs['json'], {'visibility':'archive'})
        self.assertEqual(session.request.call_args_list[2].kwargs['json']['assetIds'], ['existing'])
        self.assertNotIn('expiresAt', session.request.call_args_list[2].kwargs['json'])

    def test_duplicate_archive_permission_failure_does_not_claim_success(self):
        session = self.session(self.response(200, {'id':'existing', 'status':'duplicate'}),
                               self.response(403))
        with self.assertRaisesRegex(UploadError, 'asset.update'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session)
        self.assertEqual(session.request.call_count, 2)

    def test_auth_failure_never_attempts_share(self):
        session = self.session(self.response(403))
        with self.assertRaisesRegex(UploadError, 'permissions'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session)
        self.assertEqual(session.request.call_count, 1)

    def test_share_failure_reports_existing_asset(self):
        session = self.session(self.response(201, {'id':'asset'}), self.response(500))
        with self.assertRaisesRegex(UploadError, 'screenshot is in Immich'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session)

    def test_redirect_does_not_forward_credentials(self):
        session = self.session(self.response(302))
        with self.assertRaisesRegex(UploadError, 'redirected'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session)
        self.assertEqual(session.request.call_count, 1)

    def test_url_normalization_and_credentials_excluded_from_config(self):
        self.assertEqual(server_url('photos.example.com/'), 'https://photos.example.com')
        for url in ['file:///tmp', 'https://user:secret@example.com', 'https://host/?key=secret']:
            with self.assertRaises(ValueError): server_url(url)
        self.assertNotIn('api_key', validated({'api_key':'secret'}))

    def test_vanity_name_is_used_exactly(self):
        session = self.session(self.response(201, {'id':'asset'}),
                               self.response(201, {'key':'long-key', 'slug':'project-preview'}))
        link = upload_and_share(b'png', 'photos.example.com', 'key', session=session, custom_slug='project-preview')
        self.assertEqual(link, 'https://photos.example.com/s/project-preview')
        self.assertEqual(session.request.call_args_list[1].kwargs['json']['slug'], 'project-preview')
        self.slug.assert_not_called()

    def test_automatic_collision_retries_share_without_reupload(self):
        self.slug.side_effect = ['abc123', 'def456']
        session = self.session(self.response(201, {'id':'asset'}),
                               self.response(400, {'message':'Failed to save shared link'}),
                               self.response(201, {'key':'long-key', 'slug':'def456'}))
        link = upload_and_share(b'png', 'photos.example.com', 'key', session=session)
        self.assertEqual(link, 'https://photos.example.com/s/def456')
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual(session.request.call_args_list[2].args[1], 'https://photos.example.com/api/shared-links')

    def test_taken_vanity_name_is_not_silently_changed(self):
        session = self.session(self.response(201, {'id':'asset'}),
                               self.response(400, {'message':'Failed to save shared link'}))
        with self.assertRaisesRegex(UploadError, 'already taken'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session, custom_slug='mine')
        self.assertEqual(session.request.call_count, 2)

    def test_invalid_vanity_never_uploads(self):
        for slug in ['', '../private', 'name with spaces', 'a'*65]:
            session = self.session()
            with self.assertRaises(ValueError):
                upload_and_share(b'png', 'photos.example.com', 'key', session=session, custom_slug=slug)
            session.request.assert_not_called()

    def test_unsupported_slug_response_does_not_return_a_broken_link(self):
        session = self.session(self.response(201, {'id':'asset'}),
                               self.response(201, {'key':'long-key', 'slug':None}))
        with self.assertRaisesRegex(UploadError, 'did not accept the short URL'):
            upload_and_share(b'png', 'photos.example.com', 'key', session=session)

    def test_real_generator_is_six_url_safe_characters(self):
        for _ in range(20):
            self.assertRegex(self.original_slug(), r'^[a-z0-9]{6}$')


if __name__ == '__main__': unittest.main()
