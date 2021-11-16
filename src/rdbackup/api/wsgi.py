import os.path
import logging
import json
from werkzeug.wrappers import Request, Response
from werkzeug.utils import redirect
from werkzeug.urls import url_parse
from werkzeug.serving import run_simple
from werkzeug.routing import Map, Rule
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.exceptions import HTTPException, NotFound
from jinja2 import Environment, FileSystemLoader
from rdbackup.api import formatter
from rdbackup.api import database
from rdbackup.utils import convenience

class ApiServer:

    ## INITIALISATION
    ## ------------------------------------------------------------------------

    def __init__ (self):
        self.address          = "127.0.0.1"
        self.port             = 8080
        self.db               = database.SparqlInterface()

        ## Routes to all API calls.
        ## --------------------------------------------------------------------

        self.url_map = Map([
            Rule("/",                                  endpoint = "home"),
            Rule("/v2/account/applications/authorize", endpoint = "authorize"),
            Rule("/v2/token",                          endpoint = "token"),
            Rule("/v2/articles",                       endpoint = "articles"),
            Rule("/v2/collections",                    endpoint = "collections"),
            Rule("/v2/articles/search",                endpoint = "articles_search"),
        ])

        ## Routes for static resources.
        ## --------------------------------------------------------------------

        self.jinja   = Environment(loader = FileSystemLoader(
                        os.path.join(os.path.dirname(__file__),
                                     "resources/templates")),
                                   autoescape = True)

        self.wsgi    = SharedDataMiddleware(self.wsgi, {
            "/static": os.path.join(os.path.dirname(__file__),
                                    "resources/static")
        })

        ## Disable werkzeug logging.
        ## --------------------------------------------------------------------
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.setLevel(logging.ERROR)

    ## WSGI AND WERKZEUG SETUP.
    ## ------------------------------------------------------------------------

    def __call__ (self, environ, start_response):
        return self.wsgi (environ, start_response)

    def render_template (self, template_name, **context):
        template = self.jinja.get_template (template_name)
        return Response(template.render(context), mimetype='text/html; charset=utf-8')

    def dispatch_request (self, request):
        adapter = self.url_map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
            return getattr(self, f"api_{endpoint}")(request, **values)
        except NotFound:
            return self.error_404 (request)
        except HTTPException as e:
            logging.error(f"Unknown error in dispatch_request: {e}")
            return e

    def wsgi (self, environ, start_response):
        request  = Request(environ)
        response = self.dispatch_request(request)
        return response(environ, start_response)

    def start (self):
        run_simple (self.address,
                    self.port,
                    self,
                    use_debugger=True,
                    use_reloader=False)

    ## ERROR HANDLERS
    ## ------------------------------------------------------------------------

    def error_404 (self, request):
        if self.accepts_html (request):
            return self.render_template ("404.html")
        else:
            response = Response(json.dumps({
                "message": "This call does not exist."
            }), mimetype='application/json; charset=utf-8')
        response.status_code = 404
        return response

    def error_405 (request, allowed_methods):
        response = Response(f"Acceptable methods: {allowed_methods}",
                            mimetype="text/plain")
        response.status_code = 405
        return response

    def error_406 (request, allowed_formats):
        response = Response(f"Acceptable formats: {allowed_formats}",
                            mimetype="text/plain")
        response.status_code = 406
        return response

    ## CONVENIENCE PROCEDURES
    ## ------------------------------------------------------------------------

    def accepts_html (self, request):
        acceptable = request.headers['Accept']
        if not acceptable:
            return False
        else:
            return "text/html" in acceptable

    def accepts_json (self, request):
        acceptable = request.headers['Accept']
        if not acceptable:
            return False
        else:
            return (("application/json" in acceptable) or
                    ("*/*" in acceptable))

    def get_parameter (self, request, parameter):
        try:
            return request.form[parameter]
        except KeyError:
            return request.args.get(parameter)

    ## API CALLS
    ## ------------------------------------------------------------------------

    def api_home (self, request):
        if self.accepts_html (request):
            return self.render_template ("home.html")
        else:
            logging.info(f"Request: {request.environ}.")
            print(f"Environment: {request.environ}.")
            print(f"Headers: {request.headers['Accept']}.")

            return Response(json.dumps({ "status": "OK" }),
                            mimetype='application/json; charset=utf-8')

    def api_authorize (self, request):
        return False

    def api_token (self, request):
        return False

    def api_articles (self, request):
        if request.method != 'GET':
            return self.error_405 ("GET")
        elif not self.accepts_json(request):
            return self.error_406 ("application/json")
        else:
            ## TODO: Setting "limit" to "TEST" crashes the app. Do type checking
            ## and sanitization.

            ## Parameters
            ## ----------------------------------------------------------------
            page            = self.get_parameter (request, "page")
            page_size       = self.get_parameter (request, "page_size")
            limit           = self.get_parameter (request, "limit")
            offset          = self.get_parameter (request, "offset")
            order           = self.get_parameter (request, "order")
            order_direction = self.get_parameter (request, "order_direction")
            institution     = self.get_parameter (request, "institution")
            published_since = self.get_parameter (request, "published_since")
            modified_since  = self.get_parameter (request, "modified_since")
            group           = self.get_parameter (request, "group")
            resource_doi    = self.get_parameter (request, "resource_doi")
            item_type       = self.get_parameter (request, "item_type")
            doi             = self.get_parameter (request, "doi")
            handle          = self.get_parameter (request, "handle")

            records = self.db.articles(#page=page,
                                       #page_size=page_size,
                                       limit=limit,
                                       order=order,
                                       order_direction=order_direction,
                                       institution=institution,
                                       published_since=published_since,
                                       modified_since=modified_since,
                                       group=group,
                                       resource_doi=resource_doi,
                                       item_type=item_type,
                                       doi=doi,
                                       handle=handle)
            output = None
            try:
                output = list(map (formatter.format_article_record, records))
            except TypeError:
                output = []

            return Response(json.dumps(output),
                            mimetype='application/json; charset=utf-8')

    def api_articles_search (self, request):
        if request.method != 'POST':
            return self.error_405 ("POST")
        elif not self.accepts_json(request):
            return self.error_406 ("application/json")
        else:
            parameters = request.get_json()
            records = self.db.articles(
                limit           = convenience.value_or_none(parameters, "limit"),
                order           = convenience.value_or_none(parameters, "order"),
                order_direction = convenience.value_or_none(parameters, "order_direction"),
                institution     = convenience.value_or_none(parameters, "institution"),
                published_since = convenience.value_or_none(parameters, "published_since"),
                modified_since  = convenience.value_or_none(parameters, "modified_since"),
                group           = convenience.value_or_none(parameters, "group"),
                resource_doi    = convenience.value_or_none(parameters, "resource_doi"),
                item_type       = convenience.value_or_none(parameters, "item_type"),
                doi             = convenience.value_or_none(parameters, "doi"),
                handle          = convenience.value_or_none(parameters, "handle"),
                search_for      = convenience.value_or_none(parameters, "search_for")
            )
            output  = []
            try:
                output = list(map (formatter.format_article_record, records))
            except TypeError:
                logging.error("api_articles_search: A TypeError occurred.")

            return Response(json.dumps(output),
                            mimetype='application/json; charset=utf-8')