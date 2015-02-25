#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Plecost: Wordpress finger printer tool.
#
# @url: http://iniqua.com/labs/
# @url: https://github.com/iniqua/plecost
#
# @author:Francisco J. Gomez aka ffranz (http://iniqua.com/)
# @author:Daniel Garcia aka cr0hn (http://www.cr0hn.com/me/)
#
# Code is licensed under -- GPLv2, http://www.gnu.org/licenses/gpl.html --
#




__all__ = ["is_remote_a_wordpress", "get_wordpress_version"]

import re
import asyncio

from urllib.parse import urljoin

from ..db import DB
from ..wordlist import get_wordlist
from ..data import PlecostWordPressInfo
from ..utils import get_diff_ratio, update_progress, download, colorize


# ----------------------------------------------------------------------
# WordPress testing functions
# ----------------------------------------------------------------------
@asyncio.coroutine
def is_remote_a_wordpress(base_url, error_page, downloader):
    """
    This functions checks if remote host contains a WordPress installation.

    :param base_url: Base url
    :type base_url: basestring

    :param error_page: error page content
    :type error_page: basestring

    :param downloader: download function. This function must accept only one parameter: the URL
    :type downloader: function

    :return: True if target contains WordPress installation. False otherwise.
    :rtype: bool
    """
    total_urls = 0
    urls_found = 0

    for url in update_progress(get_wordlist("wordpress_detection.txt"), prefix_text="   "):
        total_urls += 1
        # Fix the url for urljoin
        path = url[1:] if url.startswith("/") else url

        headers, status, content = yield from downloader(urljoin(base_url, path))

        if status == 200:

            # Try to detect non-default error pages
            ratio = get_diff_ratio(content, error_page)
            if ratio < 0.35:
                urls_found += 1

    # If Oks > 85% continue
    if (urls_found / float(total_urls)) < 0.85:
        headers, status, content = yield from downloader(urljoin(base_url, "/wp-admin/"))
        if status == 302 and "wp-login.php?redirect_to=" in headers.get("location", ""):
            return True
        elif status == 301 and "/wp-admin/" in headers.get("location", ""):
            return True
        elif status == 200:
            return True
        else:
            return False
    else:
        return True


# ----------------------------------------------------------------------
@asyncio.coroutine
def get_wordpress_version(url, downloader):
    """
    This functions checks remote WordPress version.

    :param url: site to looking for WordPress version
    :type url: basestring

    :param downloader: download function. This function must accept only one parameter: the URL
    :type downloader: function

    :return: PlecostWordPressInfo instance.
    :rtype: `PlecostWordPressInfo`
    """
    url_version = {
        # Generic
        "wp-login.php": r"(;ver=)([0-9\.]+)([\-a-z]*)",

        # For WordPress 3.8
        "wp-admin/css/wp-admin-rtl.css": r"(Version[\s]+)([0-9\.]+)",
        "wp-admin/css/wp-admin.css": r"(Version[\s]+)([0-9\.]+)"
    }

    # --------------------------------------------------------------------------
    #
    # Get installed version:
    #
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # Method 1: Looking for in readme.txt
    # --------------------------------------------------------------------------
    headers, status, curr_content = yield from downloader(urljoin(url, "/readme.html"))

    curr_ver = None
    if curr_content is not None:

        curr_ver = re.search(r"""(<br[\s]*/>[\s]*[Vv]ersion[\s]*)([\d]\.[\d]\.*[\d]*)""", curr_content)
        if curr_ver is None:
            curr_ver = None
        else:
            if len(curr_ver.groups()) != 2:
                curr_ver = None
            else:
                curr_ver = curr_ver.group(2)

    # --------------------------------------------------------------------------
    # Method 1: Looking for meta tag
    # --------------------------------------------------------------------------
    _, _, curr_content_2 = yield from downloader(url)

    # Try to find the info
    cur_ver_2 = None
    if curr_content_2 is not None:
        cur_ver_2 = re.search(r'''(<meta name=\"generator\" content=\"WordPress[\s]+)([0-9\.]+)''', curr_content_2)
        if cur_ver_2 is None:
            cur_ver_2 = None
        else:
            if len(cur_ver_2.groups()) != 2:
                cur_ver_2 = None
            else:
                cur_ver_2 = cur_ver_2.group(2)

    # --------------------------------------------------------------------------
    # Match versions of the different methods
    # --------------------------------------------------------------------------
    return_current_version = "unknown"
    if curr_ver is None and cur_ver_2 is None:
        return_current_version = "unknown"
    elif curr_ver is None and cur_ver_2 is not None:
        return_current_version = cur_ver_2
    elif curr_ver is not None and cur_ver_2 is None:
        return_current_version = curr_ver
    elif curr_ver is not None and cur_ver_2 is not None:
        if curr_ver != cur_ver_2:
            return_current_version = cur_ver_2
        else:
            return_current_version = curr_ver
    else:
        return_current_version = "unknown"

    # If Current version not found
    if return_current_version == "unknown":
        for url_pre, regex in url_version.items():
            # URL to find wordpress version
            url_current_version = urljoin(url, url_pre)
            _, _, current_version_content = yield from download(url_current_version, auto_redirect=False)

            # Find the version
            if current_version_content is not None:
                tmp_version = re.search(regex, current_version_content)

                if tmp_version is not None:
                    return_current_version = tmp_version.group(2)
                    break  # Found -> stop search

    # --------------------------------------------------------------------------
    # Get last version
    # --------------------------------------------------------------------------

    # URL to get last version of WordPress available
    _, _, last_version_content = yield from downloader("https://wordpress.org/download/")

    last_version = re.search("(WordPress\&nbsp\;)([0-9\.]*)", str(last_version_content))
    if last_version is None:
        last_version = "unknown"
    else:
        if len(last_version.groups()) != 2:
            last_version = "unknown"
        else:
            last_version = last_version.group(2)

    return PlecostWordPressInfo(current_version=return_current_version,
                                last_version=last_version)


# ----------------------------------------------------------------------
def get_wordpress_vulnerabilities(wordpress_info, db):
    """
    Get CVEs associated to the installed Wordpress version

    :param wordpress_info: PlecostWordPressInfo instance
    :type wordpress_info: PlecostWordPressInfo

    :param db: cve database instance
    :type db: DB

    """
    if not isinstance(wordpress_info, PlecostWordPressInfo):
        raise TypeError("Expected PlecostWordPressInfo, got '%s' instead" % type(wordpress_info))
    if not isinstance(db, DB):
        raise TypeError("Expected DB, got '%s' instead" % type(db))

    _current_version = wordpress_info.current_version

    if _current_version is "unknown" or \
            not _current_version:
        return "\n".join([])

    cves = db.query_wordpress(_current_version)

    # Print CVE list
    res = []
    res_append = res.append
    if cves:
        res_append("\n    |_CVE list:")
        for cve in cves:
            text = "    |__%(cve)s: (http://cve.mitre.org/cgi-bin/cvename.cgi?name=%(cve)s)" % \
                   {"cve": colorize(cve, "red")}

            res_append(text)

    return "\n".join(res)