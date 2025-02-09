#!/usr/bin/env python

import argparse
import os
import sys
import logging

from cdislogging import get_logger

from fence.jwt import keys
from fence.config import config
from fence.scripting.fence_create import (
    JWTCreator,
    create_client_action,
    create_or_update_google_bucket,
    create_google_logging_bucket,
    create_sample_data,
    delete_client_action,
    delete_users,
    delete_expired_google_access,
    cleanup_expired_ga4gh_information,
    google_init,
    list_client_action,
    link_external_bucket,
    link_bucket_to_project,
    modify_client_action,
    notify_problem_users,
    remove_expired_google_accounts_from_proxy_groups,
    remove_expired_google_service_account_keys,
    sync_users,
    download_dbgap_files,
    delete_expired_service_accounts,
    verify_bucket_access_group,
    verify_user_registration,
    force_update_google_link,
    migrate_database,
    google_list_authz_groups,
    access_token_polling_job,
)
from fence.settings import CONFIG_SEARCH_FOLDERS

from gen3authz.client.arborist.client import ArboristClient


def str2bool(v):
    if v.lower() == "true":
        return True
    elif v.lower() == "false":
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected.")


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path", default="/var/www/fence/", help="path to find configuration"
    )
    parser.add_argument(
        "--arborist",
        help="the base URL for the arborist service to sync to",
        default=None,
    )

    subparsers = parser.add_subparsers(title="action", dest="action")

    create = subparsers.add_parser("create")
    create.add_argument("yaml-file-path", help="Path to a YAML file")

    client_create = subparsers.add_parser("client-create")
    client_create.add_argument("--client", required=True)
    client_create.add_argument("--urls", nargs="+")
    client_create.add_argument(
        "--username",
        help="user(can represent an organization) that owns the client",
    )
    client_create.add_argument(
        "--external",
        help="DEPRECATED. is this an external oidc client",
        action="store_true",
        default=False,
    )
    client_create.add_argument(
        "--auto-approve",
        help="whether oidc process skips user consent step",
        action="store_true",
        default=False,
    )
    client_create.add_argument(
        "--grant-types",
        help="which OAuth2 grant types are enabled for this client (default: authorization_code and refresh_token)",
        nargs="+",
    )
    client_create.add_argument(
        "--public",
        help="whether OAuth2 client should be public (no client secret)",
        action="store_true",
        default=False,
    )
    client_create.add_argument(
        "--policies", help="which ABAC policies are granted to this client", nargs="*"
    )
    client_create.add_argument(
        "--allowed-scopes", help="which scopes are allowed for this client", nargs="+"
    )

    client_modify = subparsers.add_parser("client-modify")
    client_modify.add_argument("--client", required=True)
    client_modify.add_argument("--urls", required=False, nargs="+")
    client_modify.add_argument("--name", required=False)
    client_modify.add_argument("--description", required=False)
    client_modify.add_argument("--allowed-scopes", required=False, nargs="+")
    client_modify.add_argument(
        "--append",
        help="append either new allowed scopes or urls instead of replacing",
        action="store_true",
        default=False,
    )
    client_modify.add_argument(
        "--set-auto-approve",
        help="set the oidc process to skip user consent step",
        action="store_true",
        default=False,
    )
    client_modify.add_argument(
        "--unset-auto-approve",
        help="set the oidc process to not skip user consent step",
        action="store_true",
        default=False,
    )
    client_modify.add_argument(
        "--delete-urls", help="delete all urls", action="store_true", default=False
    )
    client_modify.add_argument(
        "--policies",
        help="which ABAC policies are granted to this client; if given, "
        "previous policies will be revoked",
        nargs="*",
    )

    client_list = subparsers.add_parser("client-list")

    client_delete = subparsers.add_parser("client-delete")
    client_delete.add_argument("--client", required=True)

    user_delete = subparsers.add_parser("user-delete")
    user_delete.add_argument("--users", required=True, nargs="+")

    subparsers.add_parser("expired-service-account-delete")
    subparsers.add_parser("bucket-access-group-verify")
    subparsers.add_parser("delete-expired-google-access")
    subparsers.add_parser("cleanup-expired-ga4gh-information")

    hmac_create = subparsers.add_parser("hmac-create")
    hmac_create.add_argument("yaml-input")

    dbgap_sync = subparsers.add_parser("sync")
    dbgap_sync.add_argument(
        "--projects", dest="project_mapping", help="Specify project mapping yaml file"
    )
    dbgap_sync.add_argument("--yaml", help="Sync from yaml file")
    dbgap_sync.add_argument("--csv_dir", help="specify csv file directory")
    dbgap_sync.add_argument(
        "--sync_from_dbgap", help="sync from dbgap server True/False", default="False"
    )
    dbgap_sync.add_argument(
        "--arborist",
        help="the base URL for the arborist service to sync to",
        default=None,
    )
    dbgap_sync.add_argument(
        "--folder",
        required=False,
        help="destination where dbGaP whitelist files are saved",
        default=None,
    )

    dbgap_download = subparsers.add_parser("dbgap-download-access-files")
    dbgap_download.add_argument(
        "--folder",
        required=False,
        help="destination where dbGaP whitelist files are saved",
        default=None,
    )

    bucket_link_to_project = subparsers.add_parser("link-bucket-to-project")
    bucket_link_to_project.add_argument(
        "--bucket_id", required=True, help="ID or name for the bucket"
    )
    bucket_link_to_project.add_argument(
        "--bucket_provider", required=True, help="CloudProvider.name for the bucket"
    )
    bucket_link_to_project.add_argument(
        "--project_auth_id", required=True, help="Project.auth_id to link to bucket"
    )

    google_bucket_create = subparsers.add_parser("google-bucket-create")
    google_bucket_create.add_argument(
        "--unique-name",
        required=True,
        help="Name for the bucket, must be globally unique throughout Google",
    )
    google_bucket_create.add_argument(
        "--storage-class",
        default=None,
        help='Currently must be one of the following: "MULTI_REGIONAL", '
        '"REGIONAL", "NEARLINE", "COLDLINE", "STANDARD"',
    )
    google_bucket_create.add_argument(
        "--public",
        default=None,
        help="whether or not the bucket should be open to the public."
        "WARNING: not providing this field will leave the bucket IAM policy"
        "untouched. to set or reset the policy use: "
        "--public True or --public False",
    )
    google_bucket_create.add_argument(
        "--requester-pays",
        action="store_true",
        default=False,
        help="Whether or not to enable requester_pays on the bucket",
    )
    google_bucket_create.add_argument(
        "--google-project-id",
        default=None,
        help="Google project this bucket should be associated with",
    )
    google_bucket_create.add_argument(
        "--project-auth-id",
        default=None,
        help="a Project.auth_id to associate this bucket with. "
        "The project must exist in the db already.",
    )
    google_bucket_create.add_argument(
        "--access-logs-bucket",
        default=None,
        help="Enables logging. Must provide a Google bucket name "
        "which will store the access logs",
    )
    google_bucket_create.add_argument(
        "--allowed-privileges",
        default=None,
        nargs="*",
        help="A list of allowed privileges ex: --allowed-privileges admin "
        "read write. Currently create a Google Bucket Access Group per "
        "privilege.",
    )

    external_bucket_create = subparsers.add_parser("link-external-bucket")
    external_bucket_create.add_argument(
        "--bucket-name",
        required=True,
        help="Name for the bucket, must be globally unique throughout Google",
    )

    google_logging_bucket_create = subparsers.add_parser("google-logging-bucket-create")
    google_logging_bucket_create.add_argument(
        "--unique-name",
        required=True,
        help="Name for the bucket, must be globally unique throughout Google",
    )
    google_logging_bucket_create.add_argument(
        "--storage-class",
        default=None,
        help='Currently must be one of the following: "MULTI_REGIONAL", '
        '"REGIONAL", "NEARLINE", "COLDLINE", "STANDARD"',
    )
    google_logging_bucket_create.add_argument(
        "--google-project-id",
        default=None,
        help="Google project this bucket should be associated with. "
        "If not given, will attempt to determine from provided credentials.",
    )

    manage_google_keys = subparsers.add_parser("google-manage-keys")
    init_google = subparsers.add_parser("google-init")
    manage_user_registrations = subparsers.add_parser(
        "google-manage-user-registrations"
    )
    manage_google_accounts = subparsers.add_parser("google-manage-account-access")

    token_create = subparsers.add_parser("token-create")
    token_create.add_argument("--kid", help="key ID to use for signing tokens")
    token_create.add_argument(
        "--keys-dir",
        help=(
            "directory the RSA keys live in; defaults to `keys/` in the root"
            " directory for fence"
        ),
    )
    token_create.add_argument(
        "--type", required=True, help='type of token to create ("access" or "refresh")'
    )
    token_create.add_argument(
        "--username", required=True, help="username to generate the token for"
    )
    token_create.add_argument(
        "--scopes",
        required=True,
        help='scopes to include in the token (e.g. "user" or "data")',
    )
    token_create.add_argument("--exp", help="time in seconds until token expiration")

    force_link_google = subparsers.add_parser("force-link-google")
    force_link_google.add_argument(
        "--username", required=True, help="User to link with"
    )
    force_link_google.add_argument(
        "--google-email", required=True, help="Email to link to"
    )
    force_link_google.add_argument(
        "--expires_in",
        required=False,
        help="The time (in seconds) during which the Google account has bucket access (7 days max/default)",
    )

    notify_problem_users = subparsers.add_parser("notify-problem-users")
    notify_problem_users.add_argument(
        "--emails", required=True, nargs="+", help="List of emails to check/notify"
    )
    notify_problem_users.add_argument(
        "--auth_ids",
        required=True,
        nargs="+",
        help="List of project auth_ids to check access to",
    )
    notify_problem_users.add_argument(
        "--check_linking",
        required=False,
        default=False,
        help="True if you want to check that each email has a linked google account",
    )
    notify_problem_users.add_argument(
        "--google_project_id",
        required=True,
        help="Google Project id that all users belong to",
    )

    subparsers.add_parser("migrate", help="Migrate the fence database")
    subparsers.add_parser(
        "google-list-authz-groups",
        help="List the Google Buckets "
        "Fence is providing access to. Includes Fence Project.auth_id and Google Bucket "
        "Access Group",
    )
    update_visas = subparsers.add_parser(
        "update-visas",
        help="Update visas and refresh tokens for users with valid visas and refresh tokens.",
    )
    update_visas.add_argument(
        "--chunk-size",
        required=False,
        help="size of chunk of users we want to take from each query to db. Default value: 10",
    )
    update_visas.add_argument(
        "--concurrency",
        required=False,
        help="number of concurrent users going through the visa update flow. Default value: 5",
    )
    update_visas.add_argument(
        "--thread-pool-size",
        required=False,
        help="number of Docker container CPU used for jwt verifcation. Default value: 3",
    )
    update_visas.add_argument(
        "--buffer-size", required=False, help="max size of queue. Default value: 10"
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    # get database information
    sys.path.append(args.path)

    # replicate cfg loading done in flask app to maintain backwards compatibility
    # TODO (DEPRECATE LOCAL_SETTINGS): REMOVE this when putting cfg in
    # settings/local_settings is deprecated
    import flask

    settings_cfg = flask.Config(".")
    settings_cfg.from_object("fence.settings")
    config.update(dict(settings_cfg))

    # END - TODO (DEPRECATE LOCAL_SETTINGS): REMOVE

    config.load(search_folders=CONFIG_SEARCH_FOLDERS)

    DB = os.environ.get("FENCE_DB") or config.get("DB")

    # attempt to get from settings, this is backwards-compatibility for integration
    # tests
    if DB is None:
        try:
            from fence.settings import DB
        except ImportError:
            pass

    BASE_URL = os.environ.get("BASE_URL") or config.get("BASE_URL")
    ROOT_DIR = os.environ.get("ROOT_DIR") or os.path.dirname(
        os.path.dirname(os.path.realpath(__file__))
    )
    dbGaP = os.environ.get("dbGaP") or config.get("dbGaP")
    if not isinstance(dbGaP, list):
        dbGaP = [dbGaP]
    STORAGE_CREDENTIALS = os.environ.get("STORAGE_CREDENTIALS") or config.get(
        "STORAGE_CREDENTIALS"
    )

    arborist = None
    if args.arborist:
        arborist = ArboristClient(
            arborist_base_url=args.arborist,
            logger=get_logger("user_syncer.arborist_client"),
            authz_provider="user-sync",
        )

    if args.action == "create":
        yaml_input = args.__dict__["yaml-file-path"]
        create_sample_data(DB, yaml_input)
    elif args.action == "client-create":
        confidential = not args.public
        create_client_action(
            DB,
            username=args.username,
            client=args.client,
            urls=args.urls,
            auto_approve=args.auto_approve,
            grant_types=args.grant_types,
            confidential=confidential,
            arborist=arborist,
            policies=args.policies,
            allowed_scopes=args.allowed_scopes,
        )
    elif args.action == "client-modify":
        modify_client_action(
            DB,
            client=args.client,
            delete_urls=args.delete_urls,
            urls=args.urls,
            name=args.name,
            description=args.description,
            set_auto_approve=args.set_auto_approve,
            unset_auto_approve=args.unset_auto_approve,
            arborist=arborist,
            policies=args.policies,
            allowed_scopes=args.allowed_scopes,
            append=args.append,
        )
    elif args.action == "client-delete":
        delete_client_action(DB, args.client)
    elif args.action == "client-list":
        list_client_action(DB)
    elif args.action == "user-delete":
        delete_users(DB, args.users)
    elif args.action == "expired-service-account-delete":
        delete_expired_service_accounts(DB)
    elif args.action == "bucket-access-group-verify":
        verify_bucket_access_group(DB)
    elif args.action == "delete-expired-google-access":
        delete_expired_google_access(DB)
    elif args.action == "cleanup-expired-ga4gh-information":
        cleanup_expired_ga4gh_information(DB)
    elif args.action == "sync":
        sync_users(
            dbGaP,
            STORAGE_CREDENTIALS,
            DB,
            projects=args.project_mapping,
            is_sync_from_dbgap_server=str2bool(args.sync_from_dbgap),
            sync_from_local_csv_dir=args.csv_dir,
            sync_from_local_yaml_file=args.yaml,
            folder=args.folder,
            arborist=arborist,
        )
    elif args.action == "dbgap-download-access-files":
        download_dbgap_files(
            dbGaP,
            STORAGE_CREDENTIALS,
            DB,
            folder=args.folder,
        )
    elif args.action == "google-manage-keys":
        remove_expired_google_service_account_keys(DB)
    elif args.action == "google-init":
        google_init(DB)
    elif args.action == "google-manage-user-registrations":
        verify_user_registration(DB)
    elif args.action == "google-manage-account-access":
        remove_expired_google_accounts_from_proxy_groups(DB)
    elif args.action == "google-bucket-create":
        # true if true provided, false if anything else provided, leave as
        # None if not provided at all (policy will remain unchanged)
        if args.public and args.public.lower().strip() == "true":
            args.public = True
        elif args.public is not None:
            args.public = False

        create_or_update_google_bucket(
            DB,
            args.unique_name,
            storage_class=args.storage_class,
            public=args.public,
            requester_pays=args.requester_pays,
            google_project_id=args.google_project_id,
            project_auth_id=args.project_auth_id,
            access_logs_bucket=args.access_logs_bucket,
            allowed_privileges=args.allowed_privileges,
        )
    elif args.action == "google-logging-bucket-create":
        create_google_logging_bucket(
            args.unique_name,
            storage_class=args.storage_class,
            google_project_id=args.google_project_id,
        )
    elif args.action == "link-external-bucket":
        link_external_bucket(DB, name=args.bucket_name)
    elif args.action == "link-bucket-to-project":
        link_bucket_to_project(
            DB,
            bucket_id=args.bucket_id,
            bucket_provider=args.bucket_provider,
            project_auth_id=args.project_auth_id,
        )
    elif args.action == "google-list-authz-groups":
        google_list_authz_groups(DB)
    elif args.action == "token-create":
        keys_path = getattr(args, "keys-dir", os.path.join(ROOT_DIR, "keys"))
        keypairs = keys.load_keypairs(keys_path)
        # Default to the most recent one, but try to find the keypair with
        # matching ``kid`` to the argument provided.
        keypair = keypairs[-1]
        kid = getattr(args, "kid")
        if kid:
            for try_keypair in keypairs:
                if try_keypair.kid == kid:
                    keypair = try_keypair
                    break
        jwt_creator = JWTCreator(
            DB,
            BASE_URL,
            kid=keypair.kid,
            private_key=keypair.private_key,
            username=args.username,
            scopes=args.scopes,
            expires_in=args.exp,
        )
        token_type = str(args.type).strip().lower()
        if token_type == "access_token" or token_type == "access":
            print(jwt_creator.create_access_token().token)
        elif token_type == "refresh_token" or token_type == "refresh":
            print(jwt_creator.create_refresh_token().token)
        else:
            print(
                'invalid token type "{}"; expected "access" or "refresh"'.format(
                    token_type
                )
            )
            sys.exit(1)
    elif args.action == "force-link-google":
        exp = force_update_google_link(
            DB,
            username=args.username,
            google_email=args.google_email,
            expires_in=args.expires_in,
        )
        print(exp)
    elif args.action == "notify-problem-users":
        notify_problem_users(
            DB, args.emails, args.auth_ids, args.check_linking, args.google_project_id
        )
    elif args.action == "migrate":
        migrate_database()
    elif args.action == "update-visas":
        access_token_polling_job(
            DB,
            chunk_size=args.chunk_size,
            concurrency=args.concurrency,
            thread_pool_size=args.thread_pool_size,
            buffer_size=args.buffer_size,
        )


if __name__ == "__main__":
    main()
