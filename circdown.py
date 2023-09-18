#!/usr/bin/env python3
import argparse
import datetime
import humanfriendly
import io
import os
import re
import requests
import xml.etree.ElementTree as ET

DEFAULT_LANGUAGE = (os.getenv('LANG') or 'en_US').replace('-', '_').split('.', 1)[0]

class ImageInfo:
    __slots__ = ('name', 'url', 'size', 'mdate', 'board', 'language', 'version', 'type')

    RELEASE_REGEX = re.compile(r'^[0-9]+(?:\.[0-9]+)+')
    FULL_RELEASE_REGEX = re.compile(r'^[0-9]+(?:\.[0-9]+)*$')
    FILETYPE_REGEX = re.compile(r'^(.*?)((?:\.[A-Za-z][0-9A-Za-z]*)+)$')

    @property
    def is_rc(self):
        return 'rc' in self.version

    @property
    def is_alpha(self):
        return 'alpha' in self.version

    @property
    def is_release(self):
        return self.RELEASE_REGEX.match(self.version)

    @property
    def is_full_release(self):
        return self.FULL_RELEASE_REGEX.match(self.version)

    @property
    def human_size(self):
        return humanfriendly.format_size(self.size)

    def __str__(self):
        return f'{self.board} ({self.type}) - {self.language} - Version {self.version} - {self.human_size} - Modified {self.mdate:%Y-%m-%d %H:%M}'

    def __repr__(self):
        return ('ImageInfo('
            f'name={self.name!r}, '
            f'url={self.url!r}, '
            f'size={self.size!r}, '
            f'mdate={self.mdate!r}, '
            f'board={self.board!r}, '
            f'language={self.language!r}, '
            f'version={self.version!r}, '
            f'type={self.type!r}'
        ')')

    @classmethod
    def from_url(cls, url, size=None, mdate=None):
        info = requests.compat.urlparse(url)
        result = cls()
        result.url = url
        result.size = size
        result.mdate = mdate
        result.name = filename = info.path.split('/')[-1]
        name, result.type = cls.FILETYPE_REGEX.match(filename).groups()
        _, _, result.board, result.language, result.version = name.split('-', 4)
        return result

class S3:
    "Helper for navigating the S3 bucket"
    def __init__(self, bucket_url):
        self._bucket_url = bucket_url
        self._session = requests.Session()

    def list(self, path, *, delimiter='/', marker=None):
        rsp = self._session.get(self._bucket_url, params=dict(
            prefix=path, delimiter=delimiter, marker=marker
        ))

        # ET.register_namespace('aws', 'http://s3.amazonaws.com/doc/2006-03-01/')
        return ET.fromstring(rsp.content)

class FirmwareDownloader(S3):
    def __init__(self, bucket_url="https://adafruit-circuit-python.s3.amazonaws.com"):
        super().__init__(bucket_url)

    def parse_contents(self, doc):
        for cont in doc.findall('.//{*}Contents'):
            yield (
                requests.compat.urljoin(self._bucket_url, cont.find('{*}Key').text),
                int(cont.find('{*}Size').text),
                datetime.datetime.strptime(cont.find('{*}LastModified').text, '%Y-%m-%dT%H:%M:%S.%f%z'),
            )

    def parse_common_prefixes(self, doc):
        for cont in doc.findall('.//{*}CommonPrefixes'):
            yield requests.compat.urljoin(self._bucket_url, cont.find('{*}Prefix').text)

    def list_boards(self, search=None):
        boards = super().list(f'bin/')

        for key in self.parse_common_prefixes(boards):
            if search is None or search in key:
                yield key.split('/')[-2]

    def list_languages(self, board, search=None):
        languages = super().list(f'bin/{board}/')

        for key in self.parse_common_prefixes(languages):
            if search is None or search in key:
                yield key.split('/')[-2]

    def list_versions(self, board, language, search=None):
        for image in self.list_images(board, language):
            if search is None or search in image.version:
                if image.is_release:
                    yield image.version

    def list_images(self, board, language):
        images = super().list(f'bin/{board}/{language}/')

        return [
            ImageInfo.from_url(key, size, date)
            for key, size, date in self.parse_contents(images)
        ]

    def download(self, image):
        with self._session.get(image.url, stream=True) as rsp:
            with open(image.name, 'wb') as fh:
                progress = 0

                for chunk in rsp.iter_content(1<<15):
                    fh.write(chunk)
                    progress += len(chunk)

                    print('\r%12s' % humanfriendly.format_size(progress, True), end='', flush=True)

                print()

def main(args=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(title='Commands', dest='command')

    list_command = commands.add_parser('list')

    list_type = list_command.add_subparsers(dest='list_type', required=True)

    list_boards = list_type.add_parser('boards', aliases=['board'])
    list_boards.set_defaults(list_type='boards')
    list_boards.add_argument('search', nargs='?')

    list_langs = list_type.add_parser('languages', aliases=['lang', 'langs'])
    list_langs.set_defaults(list_type='languages')
    list_langs.add_argument('board')
    list_langs.add_argument('search', nargs='?')

    list_versions = list_type.add_parser('versions', aliases=['ver', 'vers'])
    list_versions.set_defaults(list_type='versions')
    list_versions.add_argument('board')
    list_versions.add_argument('language', nargs='?', default=DEFAULT_LANGUAGE)
    list_versions.add_argument('search', nargs='?')

    get_command = commands.add_parser('get')
    get_command.add_argument('board')
    get_command.add_argument('--language', '-L', default=DEFAULT_LANGUAGE)
    get_command.add_argument('--version', '-V')
    get_command.add_argument('--type', '-T')

    get_command.add_argument('--prerelease', action='store_true')
    get_command.add_argument('--latest', action='store_true')

    options = parser.parse_args(args)

    print(options)

    cp = FirmwareDownloader()

    if options.command == 'list':

        if options.list_type == 'boards':
            print(f'Boards containing "{options.search}":'
                  if options.search else 'Boards')

            for key in cp.list_boards(options.search):
                print('\t', key)

        elif options.list_type == 'languages':
            print(f'Languages for {options.board} containing "{options.search}":'
                  if options.search else f'Languages for {options.board}:')

            for key in cp.list_languages(options.board, options.search):
                print('\t', key)

        elif options.list_type == 'versions':
            print(f'Versions for {options.board} ({options.language}) containing "{options.search}":'
                  if options.search else f'Versions for {options.board} ({options.language}):')

            for key in cp.list_versions(options.board, options.language, options.search):
                print('\t', key)

        else:
            print("Nothing to do!")

    elif options.command == 'get':

        if options.type and not options.type.startswith('.'):
            options.type = '.' + options.type

        results = sorted(cp.list_images(options.board, options.language), key=lambda r: (r.mdate.date(), r.version), reverse=True)

        results = sorted(results, key=lambda r: r.type, reverse=True)
        results = sorted(results, key=lambda r: r.mdate.date(), reverse=True)

        filtered = [img for img in results
            if options.type is None or img.type.endswith(options.type)
            if options.version is None or img.version == options.version
            if options.prerelease or img.is_full_release or
               options.latest or img.is_release
        ]

        if filtered:
            selected = filtered[0]

            print(selected)
            print('\t', selected.url)
            print()

            print(f'Downloading "{selected.name}" ({selected.human_size})...')
            cp.download(selected)
            print(f'Finished.')

        else:
            print('No images found that match the specified board, language, and/or version.')

if __name__ == '__main__':
    main()
