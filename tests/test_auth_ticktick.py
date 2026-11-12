"""OAuth output regression checks without opening a browser or exchanging tokens."""
import unittest
from tasksync.auth_ticktick import _oauth_error_page


class OAuthErrorPageTests(unittest.TestCase):
    def test_untrusted_error_is_escaped_inside_fixed_markup(self):
        value = '<script>alert("x")</script>&\'onerror'
        result = _oauth_error_page(value)
        self.assertEqual(result, '<h1>Error</h1><p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;&#x27;onerror</p><p>You can close this window.</p>')
        self.assertNotIn('<script>', result)

    def test_plain_error_stays_readable(self):
        self.assertEqual(_oauth_error_page('access_denied'), '<h1>Error</h1><p>access_denied</p><p>You can close this window.</p>')
