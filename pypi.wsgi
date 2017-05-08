#!/usr/bin/python
import sys
import os
prefix = os.path.dirname(__file__)
sys.path.insert(0, prefix)
import cStringIO
import webui
import store
import config
import re
from functools import partial

store.keep_conn = True

CONFIG_FILE = os.environ.get("PYPI_CONFIG", os.path.join(prefix, 'config.ini'))


class Request:

    def __init__(self, environ, start_response):
        self.start_response = start_response
        try:
            length = int(environ.get('CONTENT_LENGTH', 0))
        except ValueError:
            length = 0
        self.rfile = cStringIO.StringIO(environ['wsgi.input'].read(length))
        self.wfile = cStringIO.StringIO()
        self.config = config.Config(CONFIG_FILE)
        self.status = None
        self.headers = []

    def set_status(self, status):
        self.status = status

    def send_response(self, code, message='no details available'):
        self.status = '%s %s' % (code, message)
        self.headers = []

    def send_header(self, keyword, value):
        self.headers.append((keyword, value))

    def set_content_type(self, content_type):
        self.send_header('Content-Type', content_type)

    def end_headers(self):
        self.start_response(self.status, self.headers)


class CacheControlMiddleware(object):

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):

        def _start_response(status, headers, exc_info=None):
            script = environ.get("SCRIPT_NAME", None)
            if script in set(["/simple"]):
                # Cache for a day in Fastly, but 10 minutes in browsers
                headers += [
                    ("Surrogate-Control", "max-age=86400"),
                    ("Cache-Control", "max-age=600, public"),
                ]
            elif script in set(["/packages"]):
                if status[:3] in ["200", "304"]:
                    # Cache for a year
                    headers += [("Cache-Control", "max-age=31557600, public")]
                else:
                    # Cache for an hour
                    headers += [("Cache-Control", "max-age=3600, public")]
            elif script in set(["/mirrors", "/security"]):
                # Cache these for a week
                headers += [("Cache-Control", "max-age=604800, public")]

            # http://www.gnuterrypratchett.com/
            headers += [("X-Clacks-Overhead", "GNU Terry Pratchett")]

            return start_response(status, headers, exc_info)

        return self.app(environ, _start_response)


class SecurityHeaderMiddleware(object):

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):

        def _start_response(status, headers, exc_info=None):
            headers += [
                ("X-Frame-Options", "deny"),
                ("X-XSS-Protection", "1; mode=block"),
                ("X-Content-Type-Options", "nosniff"),
            ]

            return start_response(status, headers, exc_info)

        return self.app(environ, _start_response)


def debug(environ, start_response):
    if environ['PATH_INFO'].startswith("/auth") and \
           "HTTP_AUTHORIZATION" not in environ:
        start_response("401 login",
                       [('WWW-Authenticate', 'Basic realm="foo"')])
        return
    start_response("200 ok", [('Content-type', 'text/plain')])
    environ = environ.items()
    environ.sort()
    for k, v in environ:
        yield "%s=%s\n" % (k, v)
    return


def application(environ, start_response):
    if "HTTP_AUTHORIZATION" in environ:
        environ["HTTP_CGI_AUTHORIZATION"] = environ["HTTP_AUTHORIZATION"]
    try:
        r = Request(environ, start_response)
        webui.WebUI(r, environ).run()
        return [r.wfile.getvalue()]
    except Exception as e:
        import traceback;traceback.print_exc()
        return ['Ooops, there was a problem (%s)' % e]
#application=debug


# Handle Caching at the WSGI layer
application = CacheControlMiddleware(application)


# Add some Security Headers to every response
application = SecurityHeaderMiddleware(application)


# pretend to be like the UWSGI configuration - set SCRIPT_NAME to the first
# part of the PATH_INFO if it's valid and remove that part from the PATH_INFO
def site_fake(app, environ, start_response):
    PATH_INFO = environ['PATH_INFO']
    m = re.match('^/(pypi|simple|daytime|serversig|mirrors|id|oauth|google_login|'
        'security|packages)(.*)', PATH_INFO)
    if not m:
        start_response("404 not found", [('Content-type', 'text/plain')])
        return ['Not Found: %s' % PATH_INFO]

    environ['SCRIPT_NAME'] = '/' + m.group(1)
    environ['PATH_INFO'] = m.group(2)

    return app(environ, start_response)


if __name__ == '__main__':
    # very simple wsgi server so we can play locally

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="port to listen on", type=int, default=8000)
    args = parser.parse_args()
    from wsgiref.simple_server import make_server
    httpd = make_server('', args.port, partial(site_fake, application))
    print("Serving on port %s..." % args.port)
    httpd.serve_forever()
