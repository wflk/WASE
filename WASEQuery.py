#!/usr/bin/python3

import argparse
from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search, Q, A
import sys

### Constants ###

QUERY_SEARCH = 1
QUERY_VALUES = 2

### Helpers ###

def add_default_aggregation(s):
    a = A("terms", field="request.url.raw", size=0)
    s.aggs.bucket("urls", a)

def print_debug(*arglist):
    if args.debug:
        print(file=sys.stderr, *arglist)

### Query Subcommands ###

def query_missing(s, field, name, methods=None, responsecodes=None, invert=False):
    # main query
    q = Q("match", ** { field: name })
    if not invert:
        q = ~q
    s.query = q

    # add filters
    ## method
    if methods:
        s = s.filter("terms", ** { 'request.method': methods })
    ## response codes
    if responsecodes:
        for rc in responsecodes:
            rcrange = rc.split("-")
            if len(rcrange) == 2:
                s = s.filter("range", ** { 'response.status': { "gte": int(rcrange[0]), "lte": int(rcrange[1]) } })
            else:
                s = s.filter("term", ** { 'response.status': rc })

    print_debug(s.to_dict())
    return s

def query_missingheader(s, headername, methods=None, responsecodes=None, invert=False):
    s = query_missing(s, 'response.headernames', headername, methods, responsecodes, invert)
    return s

def query_missingparam(s, paramname, methods=None, responsecodes=None, invert=False):
    s = query_missing(s, 'request.parameternames', paramname, methods, responsecodes, invert)
    return s

def query_headervals(s, headername):
    # match documents where given header name is present
    s.query = Q("match", ** { "response.headernames": headername })

    # 1. descent into response.headers
    # 2. filter given header
    # 3. aggregate header values
    # 4. jump back into main document
    # 5. aggregate URLs
    s.aggs.bucket("response_headers", "nested", path="response.headers")\
            .bucket("header", "filter", Q("match", ** { "response.headers.name": headername }))\
            .bucket("values", "terms", field="response.headers.value.raw", size=0)\
            .bucket("main", "reverse_nested")\
            .bucket("urls", "terms", field="request.url.raw", size=0)
    return s

def query(s, q):
    s.query = Q("query_string", query=q)
    return s

### Main ###
argparser = argparse.ArgumentParser(description="WASE Query Tool")
argparser.add_argument("--server", "-s", action="append", default="localhost", help="ElasticSearch server")
argparser.add_argument("--index", "-i", default="wase-*", help="ElasticSearch index pattern to query")
argparser.add_argument("--fields", "-f", action="append", help="Add fields to output. Prints full result instead of aggregated URLs.")
argparser.add_argument("--debug", "-d", action="store_true", help="Debugging output")
subargparsers = argparser.add_subparsers(title="Query Commands", dest="cmd")

argparser_missingheader = subargparsers.add_parser("missingheader", help="Search for URLs which responses are missing a header")
argparser_missingheader.add_argument("header", help="Name of the header")
argparser_missingheader.add_argument("--invert", "-n", action="store_true", help="Invert result, list all URLs where header is set")
argparser_missingheader.add_argument("--method", "-m", action="append", help="Restrict search to given methods")
argparser_missingheader.add_argument("--responsecode", "-c", action="append", help="Restrict search to responses with the given codes. Can be a single code (e.g. 200), a range (200-299) or wildcard (2*)")

argparser_missingparam = subargparsers.add_parser("missingparameter", help="Search for URLs where the requests are missing a parameter with the given name")
argparser_missingparam.add_argument("parameter", help="Name of parameter to search")
argparser_missingparam.add_argument("--invert", "-n", action="store_true", help="Invert result, list all URLs where header is set")
argparser_missingparam.add_argument("--method", "-m", action="append", help="Restrict search to given methods")
argparser_missingparam.add_argument("--responsecode", "-c", action="append", help="Restrict search to responses with the given codes. Can be a single code (e.g. 200), a range (200-299) or wildcard (2*)")
#argparser_missingparam.add_argument("--type", "-t", choices=["url", "body", "cookie", "xml", "xmlattr", "multipartattr", "json", "unknown"], help="Restrict search to given request parameter type")

argparser_headervals = subargparsers.add_parser("headervalues", help="Show all response header values and the URLs where the value was set")
argparser_headervals.add_argument("--urls", "-u", action="store_true", help="List URLs where header value is set")
argparser_headervals.add_argument("--max-urls", "-n", type=int, default=0, help="Maximum number of listed URLs")
argparser_headervals.add_argument("header", help="Name of the response header")

argparser_search = subargparsers.add_parser("search", help="Make arbitrary queries")
argparser_search.add_argument("query", nargs="*", default=["*"], help="Query string")

args = argparser.parse_args()
print_debug(args)

es = Elasticsearch(args.server)
s = Search(using=es).index(args.index)
r = None

querytype = None
if args.cmd == "missingheader":
    s = query_missingheader(s, args.header, args.method, args.responsecode, args.invert)
    querytype = QUERY_SEARCH
elif args.cmd == "missingparameter":
    s = query_missingparam(s, args.parameter, args.method, args.responsecode, args.invert)
    querytype = QUERY_SEARCH
elif args.cmd == "headervalues":
    s = query_headervals(s, args.header)
    querytype = QUERY_VALUES
elif args.cmd == "search":
    s = query(s, " ".join(args.query))
    querytype = QUERY_SEARCH
else:
    argparser.print_help()
    sys.exit(1)

if querytype == QUERY_SEARCH:
    if args.fields:
        print_debug(s.to_dict())
        r = s.scan()
    else:
        add_default_aggregation(s)
        print_debug(s.to_dict())
        r = s.execute()
elif querytype == QUERY_VALUES:
    print_debug(s.to_dict())
    r = s.execute()

if querytype == QUERY_SEARCH:
    if not r:
        print("No matches!")
        sys.exit(0)
    if args.fields:
        for d in r:
            print(d['request']['url'])
            for f in args.fields:
                print(f, end=": ")
                fl = f.split(".", 1)
                try:
                    if len(fl) == 2:
                        print(d[fl[0]][fl[1]])
                    else:
                        print(d[f])
                except KeyError:
                    print("-")
            print()
    else:
        for d in r.aggregations.urls.buckets:
            print(d['key'])
elif querytype == QUERY_VALUES:
    for hv in r.aggregations.response_headers.header.values.buckets:
        print(hv.key)
        if args.urls:
            urlcnt = -1
            if args.max_urls > 0:
                urlcnt = args.max_urls

            for url in hv.main.urls.buckets:
                print(url.key)
                if urlcnt >= 0:
                    urlcnt -= 1
                if urlcnt == 0:
                    break
            print()
