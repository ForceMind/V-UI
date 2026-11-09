"""Administrator DNS credential operations; all upstream checks are read-only."""
from app.data_owner import data_owner
from app.certificates.cloudflare import CloudflareClient
from app.certificates.dns_store import DNSCredentialStore, DNSStoreError, normalize_zone


class DNSCredentials:
    def __init__(self, certificates_root, *, client_factory=CloudflareClient):
        self.store = DNSCredentialStore(certificates_root)
        self.client_factory = client_factory
        self.data = self.store.root.parent.parent

    def list(self):
        with data_owner(self.data):
            return self.store.list()

    def create(self, *, label, zone_name: str, api_token):
        selected_zone = normalize_zone(zone_name)
        zone = self.client_factory(api_token).discover_zone(selected_zone)
        with data_owner(self.data):
            return self.store.create(label=label, zone_name=zone['zone_name'],
                                     zone_id=zone['zone_id'], api_token=api_token)

    def replace(self, selected, *, expected_revision, api_token):
        with data_owner(self.data):
            current = self.store.verification_snapshot(selected, expected_revision=expected_revision)
            zone = self.client_factory(api_token).discover_zone(current['zone_name'])
            if zone != {'zone_id': current['zone_id'], 'zone_name': current['zone_name']}:
                raise DNSStoreError('DNS_CREDENTIAL_ZONE_CHANGED')
            return self.store.replace(selected, expected_revision=expected_revision, api_token=api_token)

    def set_enabled(self, selected, *, expected_revision, enabled, confirmed=False):
        with data_owner(self.data):
            if enabled:
                if confirmed is not True:
                    raise DNSStoreError('DNS_CREDENTIAL_CONFIRMATION_REQUIRED')
                current = self.store.verification_snapshot(selected, expected_revision=expected_revision)
                zone = self.client_factory(current['api_token']).discover_zone(current['zone_name'])
                if zone != {'zone_id': current['zone_id'], 'zone_name': current['zone_name']}:
                    raise DNSStoreError('DNS_CREDENTIAL_ZONE_CHANGED')
            return self.store.set_enabled(selected, expected_revision=expected_revision,
                                          enabled=enabled, confirmed=confirmed)
