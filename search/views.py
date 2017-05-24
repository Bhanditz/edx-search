""" handle requests for courseware search http requests """
# This contains just the url entry points to use if desired, which currently has only one
# pylint: disable=too-few-public-methods
import logging
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.http import require_POST

from eventtracking import tracker as track
from .api import perform_search, course_discovery_search, course_discovery_filter_fields
from .initializer import SearchInitializer

# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name


def _process_pagination_values(request):
    """ process pagination requests from request parameter """
    size = 20
    page = 0
    from_ = 0
    if "page_size" in request.POST:
        size = int(request.POST["page_size"])
        max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)
        # The parens below are superfluous, but make it much clearer to the reader what is going on
        if not (0 < size <= max_page_size):  # pylint: disable=superfluous-parens
            raise ValueError(_('Invalid page size of {page_size}').format(page_size=size))

        if "page_index" in request.POST:
            page = int(request.POST["page_index"])
            from_ = page * size
    return size, from_, page


def _process_field_values(request):
    """ Create separate dictionary of supported filter values provided """
    return {
        field_key: request.POST[field_key]
        for field_key in request.POST
        if field_key in course_discovery_filter_fields()
    }


@require_POST
def do_search(request, course_id=None):
    """
    Search view for http requests

    Args:
        request (required) - django request object
        course_id (optional) - course_id within which to restrict search

    Returns:
        http json response with the following fields
            "took" - how many seconds the operation took
            "total" - how many results were found
            "max_score" - maximum score from these results
            "results" - json array of result documents

            or

            "error" - displayable information about an error that occured on the server

    POST Params:
        "search_string" (required) - text upon which to search
        "page_size" (optional)- how many results to return per page (defaults to 20, with maximum cutoff at 100)
        "page_index" (optional) - for which page (zero-indexed) to include results (defaults to 0)
    """

    # Setup search environment
    SearchInitializer.set_search_enviroment(request=request, course_id=course_id)

    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    search_term = request.POST.get("search_string", None)

    try:
        if not search_term:
            raise ValueError(_('No search term provided for search'))

        size, from_, page = _process_pagination_values(request)

        # Analytics - log search request
        track.emit(
            'edx.course.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
            }
        )

        results = perform_search(
            search_term,
            user=request.user,
            size=size,
            from_=from_,
            course_id=course_id
        )

        status_code = 200

        # Analytics - log search results before sending to browser
        track.emit(
            'edx.course.search.results_displayed',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
                "results_count": results["total"],
            }
        )

    except ValueError as invalid_err:
        results = {
            "error": unicode(invalid_err)
        }
        log.debug(unicode(invalid_err))

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )


@require_POST
def course_discovery(request):
    """
    Search for courses

    Args:
        request (required) - django request object

    Returns:
        http json response with the following fields
            "took" - how many seconds the operation took
            "total" - how many results were found
            "max_score" - maximum score from these resutls
            "results" - json array of result documents

            or

            "error" - displayable information about an error that occured on the server

    POST Params:
        "search_string" (optional) - text with which to search for courses
        "page_size" (optional)- how many results to return per page (defaults to 20, with maximum cutoff at 100)
        "page_index" (optional) - for which page (zero-indexed) to include results (defaults to 0)
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    search_term = request.POST.get("search_string", None)

    try:
        size, from_, page = _process_pagination_values(request)
        field_dictionary = _process_field_values(request)

        # Analytics - log search request
        track.emit(
            'edx.course_discovery.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
            }
        )

        results = course_discovery_search(
            search_term=search_term,
            size=size,
            from_=from_,
            field_dictionary=field_dictionary,
        )

        # Analytics - log search results before sending to browser
        track.emit(
            'edx.course_discovery.search.results_displayed',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
                "results_count": results["total"],
            }
        )

        status_code = 200

    except ValueError as invalid_err:
        results = {
            "error": unicode(invalid_err)
        }
        log.debug(unicode(invalid_err))

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    if settings.FEATURES.get('ENABLE_TAXOMAN', False):
        from taxoman_api.api import get_facet_slugs_display_order, get_facet_values_display_order
        slugs_display_order = get_facet_slugs_display_order()
        for slug, display_order in slugs_display_order.iteritems():
            if results['facets'].has_key(slug):
                results['facets'][slug]['display_order'] = display_order
                # collect facet value display order
                fv_display_orders = get_facet_values_display_order(slug)
                results['facets'][slug]['facet_values'] = fv_display_orders

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )
