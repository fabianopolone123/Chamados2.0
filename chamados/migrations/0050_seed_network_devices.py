from django.conf import settings
from django.db import migrations


NETWORK_DEVICES = [
    ('servers', '192.168.22.1', 'FORTINET', 'Firewall FortiGate 60F - CPD', '84:39:8F:72:EB:0D', ''),
    ('servers', '192.168.22.2', 'CPD-SERVER1', 'Dell PowerEdge R7525 - CPD', '14:23:F2:24:8D:90', ''),
    ('servers', '192.168.22.3', 'CPD-SERVER2', 'Dell PowerEdge R7525 - CPD', '14:23:F2:24:83:00', ''),
    ('servers', '192.168.22.4', 'CPD-SERVER3', 'Backup - TI', '20:67:7C:F1:7D:F8', ''),
    ('servers', '192.168.22.5', 'SRV-FS', 'Microsoft Corporation', '00:15:5D:16:52:05', ''),
    ('servers', '192.168.22.6', 'SRV-PJ', 'Microsoft Corporation', '00:15:5D:34:DE:03', ''),
    ('servers', '192.168.22.7', 'SRV-ERP', 'Microsoft Corporation', '00:15:5D:34:DE:4D', ''),
    ('servers', '192.168.22.8', 'SRV-AD', 'Microsoft Corporation', '00:15:5D:16:52:03', ''),
    ('servers', '192.168.22.9', 'SRV-AD2', 'Microsoft Corporation', '00:15:5D:34:DE:09', ''),
    ('servers', '192.168.22.10', 'SRV-SEG', 'Microsoft Corporation', '00:15:5D:16:7B:0E', ''),
    ('servers', '192.168.22.11', 'SRV-BAK', 'Microsoft Corporation', '00:15:5D:16:7B:0F', ''),
    ('servers', '192.168.22.12', 'STORAGE', 'Iomega Corporation', '00:D0:B8:22:27:38', ''),
    ('servers', '192.168.22.13', 'Console Ubiquiti', '', '00:15:5D:16:52:06', ''),
    ('servers', '192.168.22.14', 'DVR1', '', '98:2A:0A:F7:2A:47', ''),
    ('servers', '192.168.22.15', 'DVR2', '', '98:2A:0A:F7:29:B7', ''),
    ('servers', '192.168.22.16', 'VEEAM Virtual Lab', '', '', ''),
    ('servers', '192.168.22.17', 'SRV-CHAMADOS', '', '00:15:5D:16:52:0E', ''),
    ('servers', '192.168.22.18', 'SRV-GLPI', 'Linux - Server Dell R410', '', ''),
    ('servers', '192.168.22.19', '', '', '', ''),
    ('switches', '192.168.22.20', 'CPD-SWITCH1', 'Switch Aruba', 'EC:50:AA:33:22:F7', ''),
    ('switches', '192.168.22.21', 'CPD-SWITCH2', 'Switch Aruba', 'EC:50:AA:33:A8:7B', ''),
    ('switches', '192.168.22.22', 'CPD-SWITCH3', 'Switch Aruba', '94:60:D5:2D:69:29', ''),
    ('switches', '192.168.22.23', 'AD1-SWITCH1', 'Switch Aruba', 'EC:50:AA:33:F3:BE', ''),
    ('switches', '192.168.22.24', 'AD1-SWITCH2', 'Switch Aruba', 'EC:50:AA:33:C8:F7', ''),
    ('switches', '192.168.22.25', 'AD2-SWITCH1', 'Switch Aruba', 'EC:50:AA:33:E8:6A', ''),
    ('switches', '192.168.22.26', 'PRD-SWITCH1', 'Switch Aruba', 'BC:D7:A5:7B:BA:3B', ''),
    ('switches', '192.168.22.27', '', '', '', ''),
    ('switches', '192.168.22.28', '', '', '', ''),
    ('switches', '192.168.22.29', 'T1600G-52TS', 'Switch TP-Link Pintura', '50:D4:F7:85:B4:72', 'Admin / bpApajJ2s7ESggD7r6d53iYB'),
    ('idface_turnstiles', '192.168.22.30', '192.168.22.30', 'Control ID - IdFace', 'FC:52:CE:89:8F:A2', ''),
    ('idface_turnstiles', '192.168.22.31', '192.168.22.31', 'Control ID - IdFace - Montagem & Solda', 'FC:52:CE:8A:1A:22', ''),
    ('idface_turnstiles', '192.168.22.32', '192.168.22.32', 'Control ID - IdFace - Container', 'FC:52:CE:89:8A:8A', ''),
    ('idface_turnstiles', '192.168.22.33', '192.168.22.33', 'Control ID - IdFace', 'FC:52:CE:88:31:F1', ''),
    ('idface_turnstiles', '192.168.22.34', '192.168.22.34', 'Control ID - IdFace', 'FC:52:CE:88:31:70', ''),
    ('idface_turnstiles', '192.168.22.35', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.36', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.37', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.38', '192.168.22.38', 'Topdata Sistemas de Automacao Ltda', '00:18:E2:09:26:C4', ''),
    ('idface_turnstiles', '192.168.22.39', 'SPLENDA-50096', 'DIGIBRAS INDUSTRIA DO BRASIL S/A', '64:1C:67:9C:83:5E', ''),
    ('idface_turnstiles', '192.168.22.40', '', 'Control ID - IdFace - Catraca', 'FC:52:CE:8E:B6:40', 'usar webservice IP / admin / admin'),
    ('idface_turnstiles', '192.168.22.41', '', 'Control ID - IdFace - Catraca', 'FC:52:CE:8E:B5:AB', 'usar webservice IP / admin / admin'),
    ('idface_turnstiles', '192.168.22.42', '', 'Control ID - IdFace - Catraca', 'FC:52:CE:8E:A8:07', 'usar webservice IP / admin / admin'),
    ('idface_turnstiles', '192.168.22.43', '', 'Control ID - IdFace - Catraca', 'FC:52:CE:8E:A8:0F', 'usar webservice IP / admin / admin'),
    ('idface_turnstiles', '192.168.22.44', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.45', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.46', '', '', '', ''),
    ('idface_turnstiles', '192.168.22.47', 'TI-SERVER3 - iLO', 'Hewlett Packard Enterprise', '80:30:E0:36:04:DC', 'Administrator / RXQ6BDXB'),
    ('idface_turnstiles', '192.168.22.48', 'CPD-Server1 - Idrac', 'Dell Inc.', 'EC:2A:72:43:68:0E', 'root / Sidertec@2023'),
    ('idface_turnstiles', '192.168.22.49', 'CPD-Server2 - Idrac', 'Dell Inc.', 'EC:2A:72:43:63:FA', 'root / Sidertec@2023'),
    ('printers', '192.168.22.50', 'CTR-M320F', 'RICOH COMPANY, LTD.', '58:38:79:97:45:65', '123456789'),
    ('printers', '192.168.22.51', 'RH-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7D:BB:15', '123456789'),
    ('printers', '192.168.22.52', 'ALM-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7E:40:39', '123456789'),
    ('printers', '192.168.22.53', 'PRD-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7E:40:3A', '123456789'),
    ('printers', '192.168.22.54', 'FIN-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7E:40:09', '123456789'),
    ('printers', '192.168.22.55', 'COM-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7E:40:3C', '123456789'),
    ('printers', '192.168.22.56', 'CPR-M320F', 'RICOH COMPANY, LTD.', '58:38:79:7E:40:0E', '123456789'),
    ('printers', '192.168.22.57', 'CPR-IMC2000', 'RICOH COMPANY, LTD.', '58:38:79:34:BA:AB', ''),
    ('printers', '192.168.22.58', 'PCP-MP3055', 'RICOH COMPANY,LTD.', '00:26:73:D3:BD:6D', ''),
    ('printers', '192.168.22.59', 'PRJ-MP3055', 'RICOH COMPANY,LTD.', '00:26:73:D3:FF:23', ''),
    ('printers', '192.168.22.60', 'PCP-DCP2540', 'Brother Industries, LTD.', 'B4:22:00:B8:32:FF', ''),
    ('printers', '192.168.22.61', 'SEG-L3250', 'Seiko Epson Corporation', 'E0:BB:9E:7E:68:87', ''),
    ('printers', '192.168.22.62', '52JI90406011', 'Zebra Technologies Corp.', '00:07:4D:94:61:51', ''),
    ('printers', '192.168.22.63', 'ZT231', 'Zebra Technologies Corp.', '00:07:4D:F4:BB:E2', ''),
    ('printers', '192.168.22.64', 'PCP-PLOTTER', 'Hewlett Packard', '28:92:4A:AB:A1:AD', ''),
    ('printers', '192.168.22.65', 'PRJ-PLOTTER', 'Hewlett Packard', '28:92:4A:AC:81:85', ''),
    ('printers', '192.168.22.66', 'SEG-DCP2540', 'Brother Industries, LTD.', '3C:2A:F4:C9:85:E5', ''),
    ('printers', '192.168.22.67', 'PRD-DCP2540', 'Brother Industries, LTD.', '3C:2A:F4:A1:87:04', ''),
    ('printers', '192.168.22.68', 'DIR-DCP2540', 'Brother Industries, LTD.', 'B4:22:00:91:F2:D0', ''),
    ('printers', '192.168.22.69', 'PIN-HPM201', 'Hewlett Packard', '6C:C2:17:00:F0:3B', ''),
    ('wifi', '192.168.22.70', 'UBI-PINTURA', 'Ubiquiti Networks Inc.', '80:2A:A8:76:1C:DE', ''),
    ('wifi', '192.168.22.71', 'UBI-REFEITORIO', 'Ubiquiti Networks Inc.', '80:2A:A8:F6:EA:0B', ''),
    ('wifi', '192.168.22.72', 'UBI-ALMOXARIFADO', 'Ubiquiti Networks Inc.', '0C:EA:14:16:85:D7', ''),
    ('wifi', '192.168.22.73', 'UBI-PCP', 'Ubiquiti Networks Inc.', '60:22:32:53:9F:54', ''),
    ('wifi', '192.168.22.74', 'UBI-REUNIAO', 'Ubiquiti Networks Inc.', '70:A7:41:8C:24:51', ''),
    ('wifi', '192.168.22.75', 'UBI-PRODUCAO', 'Ubiquiti Networks Inc.', 'F0:9F:C2:BF:62:7B', ''),
    ('wifi', '192.168.22.76', 'UBI-PROJETOS', 'Ubiquiti Networks Inc.', '60:22:32:53:C1:EC', ''),
    ('wifi', '192.168.22.77', 'UNI-COMERCIAL', 'Ubiquiti Networks Inc.', '70:A7:41:8C:24:38', ''),
    ('wifi', '192.168.22.78', 'UBI-FABRICA', 'Ubiquiti Networks Inc.', '70:A7:41:8C:24:31', ''),
    ('wifi', '192.168.22.79', 'UBI-PORTARIA', 'Ubiquiti Networks Inc.', 'F4:92:BF:13:9E:4B', ''),
    ('wifi', '192.168.22.80', 'UBI-MATERIA PRIMA', 'Ubiquiti Networks Inc.', '0C:EA:14:25:55:36', ''),
    ('wifi', '192.168.22.81', 'UBI-EXPEDICAO', 'Ubiquiti Networks Inc.', '0C:EA:14:6E:E2:79', ''),
    ('wifi', '192.168.22.82', 'UBI-MATERIA PRIMA 2', 'Ubiquiti Networks Inc.', '6C:63:F8:5F:73:C7', ''),
    ('wifi', '192.168.22.83', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.84', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.85', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.86', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.87', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.88', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.89', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.90', '', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.91', 'U6 MESH PRO (patio/dir.)', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.92', 'U6 MESH PRO (patio/centro)', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.93', 'U6 MESH PRO (patio/esq.)', 'Ubiquiti Networks Inc.', '', ''),
    ('wifi', '192.168.22.94', '', '', '', ''),
    ('wifi', '192.168.22.95', '', '', '', ''),
    ('wifi', '192.168.22.96', '', '', '', ''),
    ('wifi', '192.168.22.97', '', '', '', ''),
    ('wifi', '192.168.22.98', '', '', '', ''),
    ('wifi', '192.168.22.99', '', '', '', ''),
    ('monitoring', '192.168.22.200', '', '', '', ''),
    ('monitoring', '192.168.22.100-254', '', '', '', ''),
]


def seed_network_devices(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    NetworkDevice = apps.get_model('chamados', 'NetworkDevice')
    user = (
        User.objects.filter(is_superuser=True).order_by('id').first()
        or User.objects.order_by('id').first()
    )
    if user is None:
        return

    for category, ip_address, name, manufacturer, mac_address, access in NETWORK_DEVICES:
        NetworkDevice.objects.get_or_create(
            ip_address=ip_address,
            defaults={
                'category': category,
                'name': name,
                'manufacturer': manufacturer,
                'mac_address': mac_address,
                'access': access,
                'created_by': user,
            },
        )


def unseed_network_devices(apps, schema_editor):
    NetworkDevice = apps.get_model('chamados', 'NetworkDevice')
    NetworkDevice.objects.filter(ip_address__in=[row[1] for row in NETWORK_DEVICES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0049_networkdevice'),
    ]

    operations = [
        migrations.RunPython(seed_network_devices, unseed_network_devices),
    ]
