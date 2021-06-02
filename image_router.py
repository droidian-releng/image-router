#!/usr/bin/python3
#
# Dead simple image router
#

import aiohttp
import asyncio
import json
import re
import os

from aiohttp import web

IMAGES_REPOSITORIES = [
	"droidian-images/rootfs-api28gsi-all"
]
ALLOWED_PREFIXES = tuple([
	"https://github.com/%s/" % repository
	for repository in IMAGES_REPOSITORIES
])

TARGET_SOCKET = '/run/image-router/image-router.sock'

GITHUB_PATTERN = "https://api.github.com/repos/%(repository)s/releases"

DROIDIAN_ROOTFS = re.compile("droidian-rootfs-[A-Za-z0-9]+-(?P<architecture>[a-z0-9]+)_(?P<date>[0-9]+).zip")
DROIDIAN_ADAPTATION = re.compile("droidian-adaptation-(?P<vendor>[A-Za-z0-9]+)-(?P<model>[a-z0-9A-Z\-\.\_]+)-(?P<architecture>[a-z0-9]+)_(?P<date>[0-9]+).zip")
DROIDIAN_FEATURE = re.compile("droidian-(?P<feature>[A-Za-z0-9]+)-(?P<architecture>[a-z0-9]+)_(?P<date>[0-9]+).zip")

not_allowed_regex = re.compile("[^a-z0-9_]+")

def slugify(string):
	"""
	"Slugifies" the supplied string.
	:param: string: the string to slugify
	"""

	return not_allowed_regex.sub("-", string.lower())

class Release(dict):

	def __init__(self, release_contents):

		self.release_contents = release_contents

		for asset in release_contents["assets"]:
			# Analyse the filename in order to get clues
			for regex in [
				DROIDIAN_ROOTFS,
				DROIDIAN_ADAPTATION,
				DROIDIAN_FEATURE
			]:
				match = regex.match(asset["name"])
				if match is None:
					continue

				gdict = match.groupdict()

				# Architecture is guarenteed to be there
				arch_dict = self.setdefault(gdict["architecture"], {})

				# Use vendor if available, or generic
				vendor_dict = arch_dict.setdefault(gdict.get("vendor", "generic"), {})

				# Finally set the download url
				if "model" in gdict:
					# Adaptation bundle
					_target = "adaptation-%s.zip" % gdict["model"]
				elif "feature" in gdict:
					# Feature bundle
					_target = "feature-%s.zip" % gdict["feature"]
				else:
					# Rootfs!
					_target = "rootfs.zip"

				if asset["browser_download_url"].startswith(ALLOWED_PREFIXES):
					vendor_dict[_target] = asset["browser_download_url"]

class ImageRouter(web.Application):

	def __init__(self, *args, loop=None, **kwargs):
		super().__init__(*args, loop=loop, **kwargs)

		# Sample mapping:
		# /rootfs-api28gsi-all/bullseye/version/architecture/generic/rootfs.zip
		# /rootfs-api28gsi-all/bullseye/version/architecture/generic/devtools.zip
		# /rootfs-api28gsi-all/bullseye/version/architecture/fxtec/pro1.zip
		self.mapping = {}

		self.add_routes(
			[
				web.get("/{repository}/{version}/{architecture}/{vendor}/{file}", self.request_handler)
			]
		)

	async def create_map_loop(self):
		while True:
			print("Creating mapping")
			self.mapping = await self.create_map()
			print("Map done")

			await asyncio.sleep(1 * 60 * 60)

	async def create_map(self):
		new_mapping = {}

		async with aiohttp.ClientSession() as session:
			for repository in IMAGES_REPOSITORIES:
				async with session.get(
					GITHUB_PATTERN % {
						"repository" : repository
					}
				) as response:

					repo_dict = new_mapping.setdefault(repository.split("/")[-1], {})

					if response.status != 200 or \
						"application/json" not in response.headers.get("Content-Type").split(";"):
							print(response)
							print("Unable to parse repository %s" % repository)
							continue

					content = await response.json()

					latest_stable = None
					for release in content:
						release_name = slugify(release["tag_name"])

						rel = Release(release)
						if rel:
							repo_dict[release_name] = rel

							# This is flaky
							if latest_stable is None and not "nightly" in release_name:
								latest_stable = release_name

					if latest_stable is not None:
						repo_dict["droidian-stable-latest"] = repo_dict[latest_stable]

		return new_mapping

	async def request_handler(self, request):
		try:
			i = request.match_info
			redirect_url = self.mapping[i["repository"]][i["version"]][i["architecture"]][i["vendor"]][i["file"]]
			raise web.HTTPFound(location=redirect_url)
		except KeyError as e:
			raise web.HTTPNotFound()

if __name__ == "__main__":
	loop = asyncio.get_event_loop()

	app = ImageRouter(loop=loop)
	runner = web.AppRunner(app)
	loop.run_until_complete(runner.setup())
	site = web.UnixSite(runner, TARGET_SOCKET)
	loop.run_until_complete(site.start())

	# FIXME? aiohttp#4155
	os.chmod(TARGET_SOCKET, 0o770)

	map_task = loop.create_task(app.create_map_loop())

	loop.run_forever()
