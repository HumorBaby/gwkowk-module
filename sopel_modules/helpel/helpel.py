# coding=utf-8
"""
helpel.py - Sopel Helpel Module
Copyright © 2019, dgw, technobabbl.es
Copyright © 2019, Humorous Baby, github.com/HumorBaby

Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import unicode_literals, absolute_import, division

import requests

from sopel.config.types import ValidatedAttribute, StaticSection
from sopel.logger import get_logger
from sopel.module import commands
from sopel.tools import SopelMemory


LOGGER = get_logger(__name__)
DEFAULT_HELPEL_PROVIDER_URL = 'https://helpel.humorbaby.net'


class HelpelSection(StaticSection):
    helpel_provider_url = ValidatedAttribute(
        'helpel_provider_url', default=DEFAULT_HELPEL_PROVIDER_URL
    )
    show_server_host = ValidatedAttribute(
        'show_server_host', bool, default=True
    )


def configure(config):
    config.define_section('helpel', HelpelSection)
    config.helpel.configure_setting(
        'helpel_provider_url', 'Helpel provider URL:'
    )
    config.helpel.configure_setting(
        'show_server_host',
        'Should the help command show the IRC server\'s hostname/IP '
        'in the listing?'
    )


def setup(bot):
    bot.config.define_section('helpel', HelpelSection)

    if 'helpel' not in bot.memory:
        bot.memory['helpel'] = SopelMemory({
            'listing_hash': None,
            'listing_id': None
        })


class PostingException(Exception):
    """Custom exception type for errors posting help to the chosen pastebin."""
    pass


class HelpListing(object):

    def __init__(self, botNick, helpPrefix, provider_url, serverHostname=None):
        self.data = {
            'botNick': botNick,
            'helpPrefix': helpPrefix,
            'serverHostname': serverHostname,
            # Does not get saved in listing since it is not in schema; only used
            # for caching purposes. Changes to the provider URL will change the
            # listing hash, and thus invalidate the cache. Also, its prevents
            # having to pass around a `bot` object just to get a single config
            # setting.
            'provider_url': provider_url
        }

        self.modules = {}

    def __hash__(self):
        return hash(str(self.render()))

    def add_entry(self, func):
        if 'commands' not in func.func_dict:
            return
        funcModule = func.__module__
        if funcModule not in self.modules:
            self.modules[funcModule] = {
                'moduleName': funcModule.rsplit('.', 1)[-1],
                'entries': {},
            }

        moduleEntries = self.modules[funcModule]['entries']
        if func not in moduleEntries:
            moduleEntries[func] = {
                'commands': func.commands,
                # Yo dawg, I heard you liked examples;
                # So I put an example in your example in your examples...
                'examples': [example['example'] for example in func.example] if 'example' in func.func_dict else None,
                'doc': func.__doc__
            }

    def render(self):
        final = dict(self.data)
        final['modules'] = []
        for k, v in self.modules.items():
            module = {
                'moduleName': v['moduleName'],
                'entries': []
            }
            for func, entry in v['entries'].items():
                module['entries'].append(entry)
            final['modules'].append(module)

        return final


def _requests_post_catch_errors(*args, **kwargs):
    try:
        response = requests.post(*args, **kwargs)
        response.raise_for_status()
    except (
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.RequestException,
            requests.exceptions.HTTPError
    ):
        # We re-raise all expected exception types to a generic "posting error"
        # that's easy for callers to expect, and then we pass the original
        # exception through to provide some debugging info
        LOGGER.exception('Error during POST request')
        raise PostingException('Could not communicate with remote service')

    # remaining handling (e.g. errors inside the response) is left to the caller
    return response


def collect_help(bot):
    helpListing = HelpListing(
        bot.nick,
        bot.config.core.help_prefix,
        bot.config.helpel.helpel_provider_url,
        bot.config.core.host if bot.config.helpel.show_server_host else None
    )

    for priority, rules in bot._callables.items():
        for regexp, funcs in rules.items():
            for func in funcs:
                helpListing.add_entry(func)

    return helpListing


def post_help(rendered_help_listing):
    try:
        res = _requests_post_catch_errors(
            rendered_help_listing['provider_url'] + '/api/helpListing',
            json=rendered_help_listing
        )
    except PostingException:
        raise

    try:
        response = res.json()
    except ValueError:
        LOGGER.error('Invalid response from provider: {}'.format(
            rendered_help_listing['provider_url']))
        raise PostingException('Could not parse response from provider.')

    if 'status' not in response or response['status'] != 'success':
        LOGGER.error('Error creating listing: {}'.format(response))
        raise PostingException('There was an error uploading the listing. '
                               'Please check logs for more details.')

    return response['data']['listingId']


@commands('helpel')
def helpel(bot, trigger):
    try:
        help_listing = collect_help(bot)
        if (
            not bot.memory['helpel']['listing_hash'] or
            bot.memory['helpel']['listing_hash'] != hash(help_listing) or
            requests.head(bot.config.helpel.helpel_provider_url.strip(
                '/') + '/api/helpListing/{}'.format(
                    bot.memory['helpel']['listing_id'])).status_code != 200
        ):
            listing_id = post_help(help_listing.render())
            bot.memory['helpel']['listing_hash'] = hash(help_listing)
            bot.memory['helpel']['listing_id'] = listing_id

        listing_id = bot.memory['helpel']['listing_id']
    except PostingException:
        bot.say('Sorry! Something went wrong.')
        LOGGER.exception('Error posting commands.')
        return

    listing_url = bot.config.helpel.helpel_provider_url.strip(
        '/') + '/{}'.format(listing_id)
    bot.say('I\'ve posted a list of my commands at {}.'.format(listing_url))
    return
