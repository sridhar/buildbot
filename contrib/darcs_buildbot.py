#! /usr/bin/python

# This is a script which delivers Change events from Darcs to the buildmaster
# each time a patch is pushed into a repository. Add it to the 'apply' hook
# on your canonical "central" repository, by putting something like the
# following in the _darcs/prefs/defaults file of that repository:
#
#  apply posthook /PATH/TO/darcs_buildbot.py BUILDMASTER:PORT
#  apply run-posthook
#
# (the second command is necessary to avoid the usual "do you really want to
# run this hook" prompt. Note that you cannot have multiple 'apply posthook'
# lines: if you need this, you must create a shell script to run all your
# desired commands, then point the posthook at that shell script.)
#
# Note that both Buildbot and Darcs must be installed on the repository
# machine. You will also need the Python/XML distribution installed (the
# "python2.3-xml" package under debian).

import os
import sys
import commands
import xml

from buildbot.clients import sendchange
from twisted.internet import defer, reactor
from xml.dom import minidom


def getText(node):
    return "".join([cn.data
                    for cn in node.childNodes
                    if cn.nodeType == cn.TEXT_NODE])


def getTextFromChild(parent, childtype):
    children = parent.getElementsByTagName(childtype)
    if not children:
        return ""
    return getText(children[0])


def makeChange(p):
    author = p.getAttribute("author")
    revision = p.getAttribute("hash")
    comments = (getTextFromChild(p, "name") + "\n" +
                getTextFromChild(p, "comment"))

    summary = p.getElementsByTagName("summary")[0]
    files = []
    for filenode in summary.childNodes:
        if filenode.nodeName in ("add_file", "modify_file", "remove_file"):
            filename = getText(filenode).strip()
            files.append(filename)
        elif filenode.nodeName == "move":
            from_name = filenode.getAttribute("from")
            to_name = filenode.getAttribute("to")
            files.append(to_name)

    # note that these are all unicode. Because PB can't handle unicode, we
    # encode them into ascii, which will blow up early if there's anything we
    # can't get to the far side. When we move to something that *can* handle
    # unicode (like newpb), remove this.
    author = author.encode("ascii", "replace")
    comments = comments.encode("ascii", "replace")
    files = [f.encode("ascii", "replace") for f in files]
    revision = revision.encode("ascii", "replace")

    change = {
        # note: this is more likely to be a full email address, which would
        # make the left-hand "Changes" column kind of wide. The buildmaster
        # should probably be improved to display an abbreviation of the
        # username.
        'username': author,
        'revision': revision,
        'comments': comments,
        'files': files,
        }
    return change


def getChangesFromCommand(cmd, count):
    out = commands.getoutput(cmd)
    try:
        doc = minidom.parseString(out)
    except xml.parsers.expat.ExpatError, e:
        print "failed to parse XML"
        print str(e)
        print "purported XML is:"
        print "--BEGIN--"
        print out
        print "--END--"
        sys.exit(1)

    c = doc.getElementsByTagName("changelog")[0]
    changes = []
    for i, p in enumerate(c.getElementsByTagName("patch")):
        if i >= count:
            break
        changes.append(makeChange(p))
    return changes


def getSomeChanges(count):
    cmd = "darcs changes --last=%d --xml-output --summary" % count
    return getChangesFromCommand(cmd, count)


LASTCHANGEFILE = ".darcs_buildbot-lastchange"


def findNewChanges():
    if os.path.exists(LASTCHANGEFILE):
        f = open(LASTCHANGEFILE, "r")
        lastchange = f.read()
        f.close()
    else:
        return getSomeChanges(1)
    lookback = 10
    while True:
        changes = getSomeChanges(lookback)
        # getSomeChanges returns newest-first, so changes[0] is the newest.
        # we want to scan the newest first until we find the changes we sent
        # last time, then deliver everything newer than that (and send them
        # oldest-first).
        for i, c in enumerate(changes):
            if c['revision'] == lastchange:
                newchanges = changes[:i]
                newchanges.reverse()
                return newchanges
        if 2*lookback > 100:
            raise RuntimeError("unable to find our most recent change "
                               "(%s) in the last %d changes" % (lastchange,
                                                                lookback))
        lookback = 2*lookback


def sendChanges(master):
    changes = findNewChanges()
    s = sendchange.Sender(master, None)

    d = defer.Deferred()
    reactor.callLater(0, d.callback, None)

    if not changes:
        print "darcs_buildbot.py: weird, no changes to send"
    elif len(changes) == 1:
        print "sending 1 change to buildmaster:"
    else:
        print "sending %d changes to buildmaster:" % len(changes)

    def _send(res, c):
        branch = None
        print " %s" % c['revision']
        return s.send(branch, c['revision'], c['comments'], c['files'],
                      c['username'])
    for c in changes:
        d.addCallback(_send, c)

    d.addCallbacks(s.printSuccess, s.printFailure)
    d.addBoth(s.stop)
    s.run()

    if changes:
        lastchange = changes[-1]['revision']
        f = open(LASTCHANGEFILE, "w")
        f.write(lastchange)
        f.close()


if __name__ == '__main__':
    MASTER = sys.argv[1]
    sendChanges(MASTER)
