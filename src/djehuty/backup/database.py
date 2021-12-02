"""This module provides a MySQL interface to store data fetched by the 'figshare' module."""

import xml.etree.ElementTree as ET
import ast
import logging
from datetime import datetime
import requests
from mysql.connector import connect, Error
from djehuty.utils import convenience

class DatabaseInterface:
    """
    A class that handles all interaction between a MySQL database and the
    output produced by the 'figshare' module.
    """

    def __init__(self):
        self.connection = None

    def __get_from_url (self, url: str, headers, parameters):
        """Procedure to perform a GET request to a Figshare-compatible endpoint."""
        response = requests.get(url,
                                headers = headers,
                                params  = parameters)
        if response.status_code == 200:
            return response.text

        logging.error("%s returned %d.", url, response.status_code)
        logging.error("Error message:\n---\n%s\n---", response.text)
        return False

    def __get_file_size_for_catalog (self, url, article_id):
        """Returns the file size for an OPeNDAP catalog."""
        total_filesize = 0
        metadata_url   = url.replace(".html", ".xml")
        metadata       = self.__get_from_url (metadata_url, {}, {})
        if not metadata:
            logging.info("Couldn't get metadata for %d.", article_id)
            return total_filesize

        namespaces  = { "c": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0" }
        xml_root    = ET.fromstring(metadata)
        references  = xml_root.findall(".//c:catalogRef", namespaces)

        ## Recursively handle directories.
        ## XXX: This may overflow the stack.
        if not references:
            logging.info("Catalog %s does not contain subdirectories.", url)
        else:
            for reference in references:
                suffix = reference.attrib["{http://www.w3.org/1999/xlink}href"]
                suburl = metadata_url.replace("catalog.xml", suffix)
                total_filesize += self.__get_file_size_for_catalog (suburl, article_id)

        ## Handle regular files.
        files          = xml_root.findall(".//c:dataSize", namespaces)
        if not files:
            if total_filesize == 0:
                logging.info("There are no files in %s.", url)
            return total_filesize

        for file in files:
            units = file.attrib["units"]
            size  = ast.literal_eval(file.text)
            if units == "Tbytes":
                size = size * 1000000000000
            elif units == "Gbytes":
                size = size * 1000000000
            elif units == "Mbytes":
                size = size * 1000000
            elif units == "Kbytes":
                size = size * 1000

            total_filesize += size

        return total_filesize

    def __execute_query (self, template, data):
        """Procedure to execute a SQL query."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(template, data)
            self.connection.commit()
            cursor.execute("SELECT LAST_INSERT_ID()")
            row = cursor.fetchone()
            return row[0]
        except Error as error:
            logging.error("Executing query failed. Reason:")
            logging.error(error)
            return False

    def connect (self, host, username, password, database):
        """Procedure to establish a database connection."""
        try:
            self.connection = connect(
                host     = host,
                user     = username,
                password = password,
                database = database)
            return self.connection.is_connected()

        except Error as error:
            logging.error("Could not establish connection to database. Reason:")
            logging.error(error)
            return False

    def is_connected (self):
        """Returns True when this instance is connected to a database."""
        return self.connection.is_connected()

    def insert_account (self, record):
        """Procedure to insert an account record."""

        template      = ("INSERT IGNORE INTO Account "
                         "(id, active, email, first_name, last_name, "
                         "institution_user_id, institution_id, group_id, "
                         "pending_quota_request, used_quota_public, "
                         "used_quota_private, used_quota, maximum_file_size, "
                         "quota, modified_date, created_date) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                         "%s, %s, %s, %s, %s)")

        created_date  = convenience.value_or_none (record, "created_date")
        if created_date is not None:
            created_date  = datetime.strptime(record["created_date"], "%Y-%m-%dT%H:%M:%SZ")
            created_date  = datetime.strftime (created_date, "%Y-%m-%d %H:%M:%S")

        modified_date = convenience.value_or_none (record, "modified_date")
        if modified_date is not None:
            modified_date = datetime.strptime(record["modified_date"], "%Y-%m-%dT%H:%M:%SZ")
            modified_date = datetime.strftime (modified_date, "%Y-%m-%d %H:%M:%S")

        data          = (
            record["id"],
            record["active"],
            convenience.value_or_none (record, "email"),
            convenience.value_or_none (record, "first_name"),
            convenience.value_or_none (record, "last_name"),
            convenience.value_or_none (record, "institution_user_id"),
            convenience.value_or_none (record, "institution_id"),
            convenience.value_or_none (record, "group_id"),
            convenience.value_or_none (record, "pending_quota_request"),
            convenience.value_or_none (record, "used_quota_public"),
            convenience.value_or_none (record, "used_quota_private"),
            convenience.value_or_none (record, "used_quota"),
            convenience.value_or_none (record, "maximum_file_size"),
            convenience.value_or_none (record, "quota"),
            modified_date,
            created_date
        )

        return self.__execute_query (template, data)

    def insert_institution (self, record):
        """Procedure to insert an institution record."""

        template = "INSERT IGNORE INTO Institution (id, name) VALUES (%s, %s)"
        data     = (record["institution_id"], record["name"])
        return self.__execute_query (template, data)

    def insert_author (self, record, item_id, item_type = "article"):
        """Procedure to insert an author record."""

        prefix   = "Article" if item_type == "article" else "Collection"
        template = ("INSERT IGNORE INTO Author "
                    "(id, institution_id, group_id, first_name, last_name, "
                    "full_name, job_title, is_active, is_public, url_name, "
                    "orcid_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s)")

        data     = (convenience.value_or_none (record, "id"),
                    convenience.value_or_none (record, "institution_id"),
                    convenience.value_or_none (record, "group_id"),
                    convenience.value_or_none (record, "first_name"),
                    convenience.value_or_none (record, "last_name"),
                    convenience.value_or_none (record, "full_name"),
                    convenience.value_or_none (record, "job_title"),
                    "is_active" in record and record["is_active"],
                    "is_public" in record and record["is_public"],
                    convenience.value_or_none (record, "url_name"),
                    convenience.value_or_none (record, "orcid_id"))

        if self.__execute_query (template, data):
            template = (f"INSERT IGNORE INTO {prefix}Author ({item_type}_id, "
                        "author_id) VALUES (%s, %s)")
            data     = (item_id, record["id"])

            if self.__execute_query (template, data):
                return record["id"]

        return False

    def insert_timeline (self, record):
        """Procedure to insert a timeline record."""

        template = ("INSERT IGNORE INTO Timeline "
                    "(revision, firstOnline, publisherPublication, "
                    "publisherAcceptance, posted, submission) "
                    "VALUES (%s, %s, %s, %s, %s, %s)")

        data     = (convenience.value_or_none (record, "revision"),
                    convenience.value_or_none (record, "firstOnline"),
                    convenience.value_or_none (record, "publisherPublication"),
                    convenience.value_or_none (record, "publisherAcceptance"),
                    convenience.value_or_none (record, "posted"),
                    convenience.value_or_none (record, "submission"))

        return self.__execute_query (template, data)

    def insert_category (self, record, item_id, item_type = "article"):
        """Procedure to insert a category record."""

        prefix      = "Article" if item_type == "article" else "Collection"
        template    = ("INSERT IGNORE INTO Category (id, title, "
                       "parent_id, source_id, taxonomy_id) "
                       "VALUES (%s, %s, %s, %s, %s)")
        category_id = convenience.value_or_none (record, "id")
        data        = (category_id,
                       convenience.value_or_none (record, "title"),
                       convenience.value_or_none (record, "parent_id"),
                       convenience.value_or_none (record, "source_id"),
                       convenience.value_or_none (record, "taxonomy_id"))

        self.__execute_query (template, data)

        template = (f"INSERT IGNORE INTO {prefix}Category (category_id, "
                    f"{item_type}_id) VALUES (%s, %s)")
        data     = (category_id, item_id)
        return self.__execute_query (template, data)


    def insert_tag (self, tag, item_id, item_type = "article"):
        """Procedure to insert a tag record."""

        prefix   = "Article" if item_type == "article" else "Collection"
        template = (f"INSERT IGNORE INTO {prefix}Tag (tag, {item_type}_id) "
                    "VALUES (%s, %s)")
        data     = (tag, item_id)
        return self.__execute_query (template, data)

    def insert_custom_field (self, field, item_id, item_type="article"):
        """Procedure to insert a custom_field record."""

        prefix      = "Article" if item_type == "article" else "Collection"
        template    = (f"INSERT IGNORE INTO {prefix}CustomField (name, value, "
                       "default_value, max_length, min_length, field_type, "
                       f"is_mandatory, placeholder, is_multiple, {item_type}_id) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")

        settings    = {}
        validations = {}
        if "settings" in field:
            settings = field["settings"]
            if "validations" in settings:
                validations = settings["validations"]

        default_value = None
        if "default_value" in settings:
            default_value = settings["default_value"]

        if isinstance(field["value"], list):
            retval = 0

            field_type = convenience.value_or_none (field, "field_type")
            for value in field["value"]:
                data = (field["name"],
                        default_value if value is None else value,
                        default_value,
                        convenience.value_or_none (validations, "max_length"),
                        convenience.value_or_none (validations, "min_length"),
                        field_type,
                        convenience.value_or_none (field, "is_mandatory"),
                        convenience.value_or_none (settings, "placeholder"),
                        convenience.value_or_none (settings, "is_multiple"),
                        item_id
                )

                retval = self.__execute_query (template, data)
                if field_type == "dropdown":
                    temp = (f"INSERT IGNORE INTO {prefix}CustomFieldOption "
                            f"({item_type}_custom_field_id, value)"
                            "VALUES (%s, %s)")
                    for option in settings["options"]:
                        data = (retval, option)
                        self.__execute_query (temp, data)

            return retval

        data    = (
            field["name"],
            default_value if field["value"] is None else field["value"],
            default_value,
            convenience.value_or_none (validations, "max_length"),
            convenience.value_or_none (validations, "min_length"),
            convenience.value_or_none (field, "field_type"),
            convenience.value_or_none (field, "is_mandatory"),
            convenience.value_or_none (settings, "placeholder"),
            convenience.value_or_none (settings, "is_multiple"),
            item_id
        )

        return self.__execute_query (template, data)

    def insert_collection (self, record, account_id):
        """Procedure to insert a collection record."""

        template = ("INSERT IGNORE INTO Collection (url, title, collection_id, "
                    "modified_date, created_date, published_date, doi, "
                    "citation, group_id, institution_id, description, "
                    "timeline_id, account_id, version, resource_id, "
                    "resource_doi, resource_title, resource_link, "
                    "resource_version, handle, group_resource_id, "
                    "articles_count, is_public) VALUES (%s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s)")

        collection_id  = record["id"]

        authors = convenience.value_or (record, "authors", [])
        for author in authors:
            self.insert_author (author, collection_id, item_type="collection")

        categories = convenience.value_or (record, "categories", [])
        for category in categories:
            self.insert_category (category, collection_id, "collection")

        fundings = convenience.value_or (record, "funding", [])
        for funding in fundings:
            self.insert_funding (funding,
                                 item_id   = collection_id,
                                 item_type = "collection")

        private_links = convenience.value_or (record, "private_links", [])
        for link in private_links:
            self.insert_private_links (link,
                                       item_id   = collection_id,
                                       item_type = "collection")

        timeline_id = None
        if "timeline" in record:
            timeline_id = self.insert_timeline (record["timeline"])

        tags = convenience.value_or (record, "tags", [])
        for tag in tags:
            self.insert_tag (tag, collection_id, item_type="collection")

        references = convenience.value_or (record, "references", [])
        for url in references:
            self.insert_reference (url, collection_id, item_type="collection")

        custom_fields = convenience.value_or (record, "custom_fields", [])
        for field in custom_fields:
            self.insert_custom_field (field, collection_id, item_type="collection")

        articles = convenience.value_or (record, "articles", [])
        for article in articles:
            self.insert_collection_article (collection_id, article)

        created_date = None
        if "created_date" in record and not record["created_date"] is None:
            created_date  = datetime.strptime(record["created_date"], "%Y-%m-%dT%H:%M:%SZ")
            created_date  = datetime.strftime (created_date, "%Y-%m-%d %H:%M:%S")

        modified_date = None
        if "modified_date" in record and not record["modified_date"] is None:
            modified_date = datetime.strptime(record["modified_date"], "%Y-%m-%dT%H:%M:%SZ")
            modified_date = datetime.strftime (modified_date, "%Y-%m-%d %H:%M:%S")

        published_date = None
        if "published_date" in record and not record["published_date"] is None:
            published_date = datetime.strptime(record["published_date"], "%Y-%m-%dT%H:%M:%SZ")
            published_date = datetime.strftime (published_date, "%Y-%m-%d %H:%M:%S")

        data     = (
            convenience.value_or_none(record, "url"),
            convenience.value_or_none(record, "title"),
            collection_id,
            modified_date,
            created_date,
            published_date,
            convenience.value_or_none(record, "doi"),
            convenience.value_or_none(record, "citation"),
            convenience.value_or_none(record, "group_id"),
            convenience.value_or_none(record, "institution_id"),
            convenience.value_or_none(record, "description"),
            timeline_id,
            account_id,
            convenience.value_or_none(record, "version"),
            convenience.value_or_none(record, "resource_id"),
            convenience.value_or_none(record, "resource_doi"),
            convenience.value_or_none(record, "resource_title"),
            convenience.value_or_none(record, "resource_link"),
            convenience.value_or_none(record, "resource_version"),
            convenience.value_or_none(record, "handle"),
            convenience.value_or_none(record, "group_resource_id"),
            convenience.value_or_none(record, "articles_count"),
            convenience.value_or_none(record, "public"),
        )
        if not self.__execute_query (template, data):
            logging.error("Inserting collection failed.")
            return False

        return True

    def insert_collection_article (self, collection_id, article_id):
        """Procedure to insert a collection-article relationship."""

        template = ("INSERT IGNORE INTO CollectionArticle "
                    "(collection_id, article_id) VALUES (%s, %s)")
        data     = (collection_id, article_id)

        return self.__execute_query (template, data)

    def insert_funding (self, record, item_id, item_type="article"):
        """Procedure to insert a funding record."""

        prefix   = "Article" if item_type == "article" else "Collection"
        template = (f"INSERT IGNORE INTO {prefix}Funding (id, {item_type}_id, "
                    "title, grant_code, funder_name, is_user_defined, url) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)")

        data     = (convenience.value_or_none (record, "id"),
                    item_id,
                    convenience.value_or_none (record, "title"),
                    convenience.value_or_none (record, "grant_code"),
                    convenience.value_or_none (record, "funder_name"),
                    convenience.value_or_none (record, "is_user_defined"),
                    convenience.value_or_none (record, "url"))

        if not self.__execute_query (template, data):
            logging.error("Inserting funding for collection failed.")
            return False

        return True

    def insert_private_links (self, record, item_id, item_type="article"):
        """Procedure to insert a private link to an article or a collection."""

        prefix   = "Article" if item_type == "article" else "Collection"
        template = (f"INSERT IGNORE INTO {prefix}PrivateLink "
                    "(id, {item_type}_id, is_active, expires_date) "
                    "VALUES (%s, %s, %s, %s)")

        expires_date  = convenience.value_or_none (record, "expires_date")
        if expires_date is not None:
            expires_date  = datetime.strptime(record["expires_date"], "%Y-%m-%dT%H:%M:%SZ")
            expires_date  = datetime.strftime (expires_date, "%Y-%m-%d %H:%M:%S")

        data     = (convenience.value_or_none (record, "id"),
                    item_id,
                    convenience.value_or_none (record, "is_active"),
                    expires_date)

    def insert_embargo (self, record, article_id):
        """Procedure to insert an embargo record."""

        template = ("INSERT IGNORE INTO ArticleEmbargoOption "
                    "(id, article_id, type, ip_name) "
                    "VALUES (%s, %s, %s, %s)")

        data     = (convenience.value_or_none (record, "id"),
                    article_id,
                    convenience.value_or_none (record, "type"),
                    convenience.value_or_none (record, "ip_name"))

        embargo_option_id = self.__execute_query (template, data)
        if not embargo_option_id:
            logging.error("Inserting embargo option failed.")

        return embargo_option_id

    def insert_license (self, record):
        """Procedure to insert a license record."""

        template = "INSERT IGNORE INTO License (id, name, url) VALUES (%s, %s, %s)"

        if not self.__execute_query (template, (record["value"], record["name"], record["url"])):
            logging.error("Inserting license failed.")
            return False

        return True

    def insert_statistics (self, record, article_id):
        """Procedure to insert statistics for an article."""

        template = ("INSERT INTO ArticleStatistics (article_id, views, "
                    "downloads, shares, date) VALUES (%s, %s, %s, %s, %s)")

        data = (article_id,
                convenience.value_or_none (record, "views"),
                convenience.value_or_none (record, "downloads"),
                convenience.value_or_none (record, "shares"),
                convenience.value_or_none (record, "date"))

        return self.__execute_query (template, data)

    def insert_file (self, record, article_id):
        """Procedure to insert a file record."""

        template = ("INSERT IGNORE INTO File (id, name, size, is_link_only, "
                    "download_url, supplied_md5, computed_md5, viewer_type, "
                    "preview_state, status, upload_url, upload_token) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")


        if (record["download_url"].startswith("https://opendap.4tu.nl/thredds") and
            record["size"] == 0):
            record["size"] = self.__get_file_size_for_catalog (record["download_url"], article_id)

        data = (convenience.value_or_none (record, "id"),
                convenience.value_or_none (record, "name"),
                convenience.value_or_none (record, "size"),
                convenience.value_or_none (record, "is_link_only"),
                convenience.value_or_none (record, "download_url"),
                convenience.value_or_none (record, "supplied_md5"),
                convenience.value_or_none (record, "computed_md5"),
                convenience.value_or_none (record, "viewer_type"),
                convenience.value_or_none (record, "preview_state"),
                convenience.value_or_none (record, "status"),
                convenience.value_or_none (record, "upload_url"),
                convenience.value_or_none (record, "upload_token"))

        if self.__execute_query (template, data):
            template = "INSERT IGNORE INTO ArticleFile (article_id, file_id) VALUES (%s, %s)"
            data     = (article_id, record["id"])
            if self.__execute_query (template, data):
                return record["id"]

        return False

    def insert_reference (self, url, item_id, item_type="article"):
        """Procedure to insert an article reference."""

        prefix   = "Article" if item_type == "article" else "Collection"
        template = f"INSERT IGNORE INTO {prefix}Reference ({item_type}_id, url) VALUES (%s, %s)"
        data     = (item_id, url)

        return self.__execute_query (template, data)

    def insert_article (self, record):
        """Procedure to insert an article record."""

        template = ("INSERT IGNORE INTO Article (article_id, account_id, title, doi, "
                    "handle, group_id, url, url_public_html, url_public_api, "
                    "url_private_html, url_private_api, published_date, thumb, "
                    "defined_type, defined_type_name, is_embargoed, citation, "
                    "has_linked_file, metadata_reason, confidential_reason, "
                    "is_metadata_record, is_confidential, is_public, funding, "
                    "modified_date, created_date, size, status, version, "
                    "description, figshare_url, resource_doi, resource_title, "
                    "timeline_id, license_id) VALUES (%s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")

        article_id  = record["id"]
        timeline_id = None
        if "timeline" in record:
            timeline_id = self.insert_timeline (record["timeline"])

        embargos = convenience.value_or (record, "embargo_options", [])
        for embargo in embargos:
            self.insert_embargo (embargo, article_id)

        references = convenience.value_or (record, "references", [])
        for url in references:
            self.insert_reference (url, article_id, item_type="article")

        categories = convenience.value_or (record, "categories", [])
        for category in categories:
            self.insert_category (category, article_id, "article")

        license_id = record["license"]["value"]
        self.insert_license (record["license"])

        tags = convenience.value_or (record, "tags", [])
        for tag in tags:
            self.insert_tag (tag, article_id, item_type="article")

        authors = convenience.value_or (record, "authors", [])
        for author in authors:
            self.insert_author (author, article_id, item_type="article")

        files = convenience.value_or (record, "files", [])
        for file in files:
            self.insert_file (file, article_id)

        funding_list = convenience.value_or (record, "funding_list", [])
        for funding in funding_list:
            self.insert_funding (funding,
                                 item_id   = article_id,
                                 item_type = "article")

        private_links = convenience.value_or (record, "private_links", [])
        for link in private_links:
            self.insert_private_links (link,
                                       item_id   = article_id,
                                       item_type = "article")

        if "statistics" in record:
            stats = record["statistics"]
            for day in stats:
                self.insert_statistics (day, article_id)

        created_date = None
        if "created_date" in record and not record["created_date"] is None:
            created_date  = datetime.strptime(record["created_date"], "%Y-%m-%dT%H:%M:%SZ")

        modified_date = None
        if "modified_date" in record and not record["modified_date"] is None:
            modified_date = datetime.strptime(record["modified_date"], "%Y-%m-%dT%H:%M:%SZ")

        custom_fields = record["custom_fields"]
        for field in custom_fields:
            self.insert_custom_field (field, article_id, item_type="article")

        data          = (article_id,
                         convenience.value_or_none (record, "account_id"),
                         convenience.value_or_none (record, "title"),
                         convenience.value_or_none (record, "doi"),
                         convenience.value_or_none (record, "handle"),
                         convenience.value_or_none (record, "group_id"),
                         convenience.value_or_none (record, "url"),
                         convenience.value_or_none (record, "url_public_html"),
                         convenience.value_or_none (record, "url_public_api"),
                         convenience.value_or_none (record, "url_private_html"),
                         convenience.value_or_none (record, "url_private_api"),
                         convenience.value_or_none (record, "published_date"),
                         convenience.value_or_none (record, "thumb"),
                         convenience.value_or_none (record, "defined_type"),
                         convenience.value_or_none (record, "defined_type_name"),
                         "is_embargoed" in record and record["is_embargoed"],
                         convenience.value_or_none (record, "citation"),
                         "has_linked_file" in record and record["has_linked_file"],
                         convenience.value_or_none (record, "metadata_reason"),
                         convenience.value_or_none (record, "confidential_reason"),
                         convenience.value_or_none (record, "is_metadata_record"),
                         convenience.value_or_none (record, "is_confidential"),
                         convenience.value_or_none (record, "is_public"),
                         convenience.value_or_none (record, "funding"),
                         modified_date,
                         created_date,
                         convenience.value_or_none (record, "size"),
                         convenience.value_or_none (record, "status"),
                         convenience.value_or_none (record, "version"),
                         convenience.value_or_none (record, "description"),
                         convenience.value_or_none (record, "figshare_url"),
                         convenience.value_or_none (record, "resource_doi"),
                         convenience.value_or_none (record, "resource_title"),
                         timeline_id,
                         license_id)

        if not self.__execute_query (template, data):
            logging.error("Inserting article failed.")
            return False

        return True

    def disconnect(self):
        """Procedure disconnect from the currently connected database."""

        self.connection.commit()
        cursor = self.connection.cursor()
        cursor.close()
        self.connection.close()
        return True