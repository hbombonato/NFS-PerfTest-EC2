#!/bin/bash
# Instance setup script (runs before instance reported as ready)
#
# Installs required software. Every instance gets the same thing. Wastes
# bandwidth relative to setting this up pre-deployment, but who cares.
#
# Note: this runs as root, so no need to mess with sudo
#
PACKAGES="iperf nfs-kernel-server nfs-common portmap"

apt-get -y update
apt-get -y install $PACKAGES
# No upgrades needed; we're not really worred about security

# Start iperf server
iperf -s &

# Create and mount ramdisk
mkdir /tmp/ramdisk; chmod 777 /tmp/ramdisk
mount -t tmpfs -o size=384M tmpfs /tmp/ramdisk/

# Create mount points for remote ramdisks
mkdir /mnt/remote_ramdisk_1

# Setup NFS server. No security since it is already setup at the AWS layer
EXPORT_STR="/tmp/ramdisk 10.0.0.0/8(rw,insecure,no_subtree_check,async,fsid=0)"
echo $EXPORT_STR >> /etc/exports
service nfs-kernel-server restart
