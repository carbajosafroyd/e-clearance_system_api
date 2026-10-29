from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import resolve
from rest_framework.test import APIClient, APIRequestFactory

from api.clearance.bridge_errors import ClearanceBridgeError
from api.clearance.services.adapter_compat_service import ClearanceAdapterCompatService
from api.clearance.services.n8n_gateway import N8nGateway


def assignment(**changes):
    return {
        'assignment_id': 'QA-ASSIGNMENT', 'is_signatory': True,
        'permissions': ['QUEUE_VIEW', 'CLEARANCE_VIEW'],
        'program_scope': 'ALL', 'year_scope': 'ALL', 'set_scope': 'ALL',
        'institute_id': 5, **changes,
    }


class BridgeRouteTests(SimpleTestCase):
    def test_all_flutter_routes_resolve_with_and_without_trailing_slash(self):
        routes = {
            'identity/resolve': 'clms-identity-resolve',
            'students': 'clms-students',
            'students/QA-0000001/collection-status': 'clms-student-collection-status',
            'collection/categories': 'clms-collection-categories',
            'collection/categories/current': 'clms-current-collection-categories',
            'collection/queues': 'clms-collection-queues',
            'collection/queues/QA-QUEUE': 'clms-collection-queue-detail',
            'health': 'clms-clearance-bridge-health',
        }
        for route, name in routes.items():
            for slash in ('', '/'):
                with self.subTest(route=route, slash=slash):
                    self.assertEqual(resolve('/api/v1/clearance/bridge/' + route + slash).url_name, name)

    def test_identity_get_is_method_not_allowed(self):
        response = APIClient().get('/api/v1/clearance/bridge/identity/resolve/')
        self.assertEqual(response.status_code, 405)

    def test_malformed_json_is_bad_request(self):
        response = APIClient().post('/api/v1/clearance/bridge/identity/resolve/', '{', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_non_object_identity_body_is_validation_error(self):
        for body in ([], ['email'], 'email', 123):
            with self.subTest(body=body):
                response = APIClient().post('/api/v1/clearance/bridge/identity/resolve/', body, format='json')
                self.assertEqual(response.status_code, 422)


class BridgeAuthorizationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.request = APIRequestFactory().get('/', HTTP_X_ASSIGNMENT_ID='QA-ASSIGNMENT')
        self.service = ClearanceAdapterCompatService()
        self.service.n8n = Mock()
        self.active = assignment()
        self.service.n8n.validate_session.return_value = {'authenticated': True, 'active_assignment': self.active}
        self.service.n8n.collection_context.return_value = {
            'assignment': self.active.copy(), 'institute_id': 5,
            'financial_requirements': [],
        }
        self.service._required_institute_id = Mock(return_value=5)

    def assert_error(self, code, operation):
        with self.assertRaises(ClearanceBridgeError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)

    def test_session_requires_explicit_boolean_authentication(self):
        gateway = N8nGateway()
        for value in (None, False, 'true', 'false', 1, 0):
            with self.subTest(value=value), patch.object(gateway, '_post', return_value={'data': {'authenticated': value}}):
                self.assert_error('SESSION_REQUIRED', lambda: gateway.validate_session(self.request))
        with patch.object(gateway, '_post', return_value={'data': {'active_assignment': self.active}}):
            self.assert_error('SESSION_REQUIRED', lambda: gateway.validate_session(self.request))

    def test_explicit_authenticated_session_is_preserved(self):
        gateway = N8nGateway()
        payload = {'authenticated': True, 'active_assignment': self.active}
        with patch.object(gateway, '_post', return_value={'data': payload}):
            self.assertEqual(gateway.validate_session(self.request), payload)

    def test_admin_requires_explicit_non_signatory_assignment(self):
        for value in (None, 'false', 0, True):
            with self.subTest(value=value):
                self.active.update(is_signatory=value, permissions=['USER_MANAGE'])
                self.assert_error('ADMIN_ASSIGNMENT_REQUIRED', lambda: self.service.admin_session(self.request, {'USER_MANAGE'}))
        self.active.update(is_signatory=False, permissions=['USER_MANAGE'])
        self.assertTrue(self.service.admin_session(self.request, {'USER_MANAGE'}))

    def test_signee_requires_explicit_signatory_assignment(self):
        for value in (None, 'false', 'true', 1, False):
            with self.subTest(value=value):
                self.active['is_signatory'] = value
                self.assert_error('SIGNEE_ASSIGNMENT_REQUIRED', lambda: self.service.signee_session(self.request))

    def test_permissions_must_be_an_array_of_strings(self):
        for value in ('QUEUE_VIEW', {'QUEUE_VIEW': True}, ['QUEUE_VIEW', 123]):
            with self.subTest(value=value):
                self.active['permissions'] = value
                self.assert_error('SESSION_CONTEXT_INVALID', lambda: self.service.signee_session(self.request))

    def test_cached_assignment_context_cannot_bypass_session_validation(self):
        cache.set('clms:collection-context:QA-ASSIGNMENT', self.service.n8n.collection_context.return_value, 60)
        self.service.n8n.validate_session.side_effect = ClearanceBridgeError(401, 'SESSION_REQUIRED', 'Sign in.')
        self.assert_error('SESSION_REQUIRED', lambda: self.service._context(self.request, force_refresh=False))
        self.service.n8n.collection_context.assert_not_called()

    def test_selected_assignment_must_belong_to_session(self):
        self.active['assignment_id'] = 'OTHER-ASSIGNMENT'
        self.assert_error('SIGNEE_ASSIGNMENT_REQUIRED', lambda: self.service._context(self.request, force_refresh=False))
        self.service.n8n.collection_context.assert_not_called()

    def test_upstream_collection_assignment_must_match_selected_assignment(self):
        self.service.n8n.collection_context.return_value['assignment']['assignment_id'] = 'OTHER-ASSIGNMENT'
        self.assert_error('COLLECTION_CONTEXT_INVALID', lambda: self.service._context(self.request, force_refresh=False))

    def test_context_scopes_must_match_authenticated_assignment(self):
        self.service.n8n.collection_context.return_value['assignment']['year_scope'] = 'First Year'
        self.assert_error('COLLECTION_CONTEXT_INVALID', lambda: self.service._context(self.request, force_refresh=False))

    def test_collection_context_cannot_switch_institute(self):
        self.service.n8n.collection_context.return_value['institute_id'] = 6
        self.assert_error('INSTITUTE_CONTEXT_MISMATCH', lambda: self.service._context(self.request, force_refresh=False))

    def test_administrator_directory_respects_assignment_scope_after_cache_read(self):
        self.active.update(is_signatory=False, permissions=['USER_MANAGE'], year_scope='First Year')
        first = {'program': 'BSIT', 'year_level': 'First Year', 'set_code': 'BSIT-1A'}
        second = {'program': 'BSIT', 'year_level': 'Second Year', 'set_code': 'BSIT-2A'}
        cache.set('clms:students:5', [first, second], 60)
        self.assertEqual(self.service.list_students(self.request)['students'], [first])

    def test_upstream_context_is_checked_again_on_each_request(self):
        self.service._context(self.request, force_refresh=False)
        self.service.n8n.collection_context.side_effect = ClearanceBridgeError(403, 'PERMISSION_DENIED', 'Revoked.')
        self.assert_error('PERMISSION_DENIED', lambda: self.service._context(self.request, force_refresh=False))
        self.assertEqual(self.service.n8n.validate_session.call_count, 2)

    def test_missing_scopes_never_mean_all(self):
        for field in ('program_scope', 'year_scope', 'set_scope'):
            for value in ('', None):
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.service._assignment_scope_allows(assignment(**{field: value}), 'BSIT', 'First Year', 'BSIT-1A'))

    def test_explicit_all_and_specific_scopes_work(self):
        self.assertTrue(self.service._assignment_scope_allows(assignment(), 'BSIT', 'First Year', 'BSIT-1A'))
        scoped = assignment(program_scope='BSIT', year_scope='First Year', set_scope='BSIT-1A')
        self.assertTrue(self.service._assignment_scope_allows(scoped, 'BSIT', 'First Year', 'BSIT-1A'))
        self.assertFalse(self.service._assignment_scope_allows(scoped, 'BSIT', 'Second Year', 'BSIT-2A'))

    def test_empty_configured_programs_authorize_no_programs(self):
        context = {'assignment': self.active, 'current_academic_year': '2026-2027', 'unit_programs': []}
        self.service._active_program_names = Mock(return_value=['BSIT'])
        with patch('api.clearance.services.adapter_compat_service.Student') as students, patch('api.clearance.services.adapter_compat_service.Fee'):
            self.assertEqual(self.service._current_dataset(context, force_refresh=True), ([], [], []))
            students.objects.select_related.assert_not_called()

    def test_malformed_configured_programs_fail(self):
        context = {'assignment': self.active, 'current_academic_year': '2026-2027', 'unit_programs': 'BSIT'}
        self.service._active_program_names = Mock(return_value=['BSIT'])
        self.assert_error('COLLECTION_CONTEXT_INVALID', lambda: self.service._current_dataset(context, force_refresh=True))

    def test_missing_or_malformed_requirements_are_not_an_empty_success(self):
        for context in ({}, {'financial_requirements': None}, {'financial_requirements': {}}, {'financial_requirements': [123]}):
            with self.subTest(context=context):
                self.assert_error('COLLECTION_CONTEXT_INVALID', lambda: self.service._requirements(context))
        self.assertEqual(self.service._requirements({'financial_requirements': []}), [])

    def test_missing_student_is_not_reported_financially_clear(self):
        self.service._context = Mock(return_value={'assignment': self.active, 'current_academic_year': '2026-2027', 'financial_requirements': []})
        self.service._fee_queryset = Mock()
        self.service._fee_queryset.return_value.filter.return_value = []
        with patch('api.clearance.services.adapter_compat_service.Student') as students:
            students.objects.select_related.return_value.filter.return_value.filter.return_value.first.return_value = None
            self.assert_error('STUDENT_NOT_FOUND', lambda: self.service.student_status(self.request, 'QA-0000001'))
        self.service._fee_queryset.assert_not_called()

    def test_individual_student_read_honors_assignment_scope(self):
        scoped = assignment(year_scope='First Year')
        self.service._context = Mock(return_value={'assignment': scoped, 'current_academic_year': '2026-2027', 'financial_requirements': []})
        self.service._active_program_names = Mock(return_value=['BSIT'])
        self.service._fee_queryset = Mock()
        self.service._fee_queryset.return_value.filter.return_value = []
        student = SimpleNamespace(program=SimpleNamespace(name='BSIT'), s_lvl=2, s_set='A', s_fname='QA', s_lname='Test', s_mname='', s_suffix='')
        with patch('api.clearance.services.adapter_compat_service.Student') as students:
            students.objects.select_related.return_value.filter.return_value.filter.return_value.first.return_value = student
            self.assert_error('PERMISSION_DENIED', lambda: self.service.student_status(self.request, 'QA-0000001'))
        self.service._fee_queryset.assert_not_called()

    def test_institute_mismatch_fails_closed(self):
        service = ClearanceAdapterCompatService()
        with patch.object(service, '_institute_id_from_dnsc_identity', return_value=5):
            self.assert_error('INSTITUTE_CONTEXT_MISMATCH', lambda: service._required_institute_id({'institute_id': 6}))

    def test_conflicting_top_level_and_assignment_institutes_fail_closed(self):
        service = ClearanceAdapterCompatService()
        with patch.object(service, '_institute_id_from_dnsc_identity', return_value=5):
            self.assert_error('INSTITUTE_CONTEXT_MISMATCH', lambda: service._required_institute_id({
                'institute_id': 5, 'active_assignment': {'institute_id': 6},
            }))

    def test_invalid_institute_identifiers_never_become_authority(self):
        service = ClearanceAdapterCompatService()
        with patch.object(service, '_institute_id_from_dnsc_identity', return_value=None):
            for value in (True, False, 0, -1, 1.5, 'not-an-institute'):
                with self.subTest(value=value):
                    self.assert_error('INSTITUTE_CONTEXT_INVALID', lambda: service._required_institute_id({'institute_id': value}))

    def test_missing_institute_fails_closed(self):
        service = ClearanceAdapterCompatService()
        with patch.object(service, '_institute_id_from_dnsc_identity', return_value=None):
            self.assert_error('INSTITUTE_CONTEXT_REQUIRED', lambda: service._required_institute_id({}))
