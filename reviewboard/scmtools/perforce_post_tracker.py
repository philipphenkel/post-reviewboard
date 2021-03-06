# Perforce post-commit SCM tool with revision tracking functionality
# Author: Philipp Henkel, weltraumpilot@googlemail.com

import urllib

from datetime import datetime, timedelta
from operator import itemgetter

from perforce_post import PerforcePostCommitTool, PerforcePostCommitClient
from errors import SCMError
from post_utils import get_known_revisions, RepositoryRevisionCache

from django.utils import encoding


def extract_revision_user(line):
    # Try to extract revision info tuple (rev, user, line, shelved) from line
    # Revision info example: "116855 by henkel on 2011-03-24 11:30 AM"
    words = line.split(' ',  4)

    if len(words)>=4 and words[0].isdigit() and words[1] == 'by' and words[3] == 'on':
        return (words[0], words[2], line, False)
    
    return None


class PerforcePostCommitTrackerClient(PerforcePostCommitClient):
    def __init__(self, p4port, username, password, use_stunnel=False):
        PerforcePostCommitClient.__init__(self, p4port, username, password, use_stunnel)

    def get_missing_revisions(self, userid, scm_user, revisionCache):
        """
        Returns revisions that are not yet available in Review Board.
        """
        return self._run_worker(lambda: self._get_missing_revisions(userid, scm_user, revisionCache))
                    
    def _get_missing_revisions(self, userid, scm_user, revisionCache):
        
        # Fetch user's commits from repository
        commits = revisionCache.get_latest_commits(scm_user, self._fetch_log_of_day_uncached)
        
        # Fetch the already contained
        known_revisions = get_known_revisions(scm_user, 
                                              self.p4port, 
                                              revisionCache.get_freshness_delta(), 
                                              extract_revision_user)

        changelists_to_be_ignored = revisionCache.get_ignored_revisions(userid)           
        
        # Revision exclusion predicate
        isExcluded = lambda rev : rev in known_revisions or rev in changelists_to_be_ignored
        
        #first compare by shelved or not (pos 3 in tuple), then by changenumber (pos 0 in tuple)
        sorted_revisions = sorted([ rev for rev in commits if not isExcluded(rev[0]) ], 
                                  key=itemgetter(0), 
                                  reverse=False)
        
        # Don't cache Shelved changelists
        sorted_shelved_all = sorted([(rev[0], rev[2]) for rev in self._fetch_shelved_logs(scm_user)], key=itemgetter(0))
        # Filter ignored shelved changelists
        sorted_shelved = [ rev for rev in sorted_shelved_all if not rev[0] in changelists_to_be_ignored ]
        
        # Starting with oldest entries first, return first the submitted revisions, then the shelved 
        # changelists because these are considered brand new
        return sorted_revisions + sorted_shelved
    
    
    def _get_log_changelists(self, changelists):
        log = []
        
        for changedesc in changelists:
            submit_date = datetime.fromtimestamp(int(changedesc['time']))        
            date_str = submit_date.strftime("%Y-%m-%d")
            
            msg = (changedesc['desc'].splitlines()or [''])[0].strip()
            msg = encoding.smart_str(msg, encoding='ascii', errors='ignore')
            
            shelved = 'shelved' in changedesc
            #' by ' + changedesc['user'] + 
            desc = ('shelved ' if shelved else 'on ' + date_str)  + ' : ' + msg
            log.append(( str(changedesc['change']), 
                         changedesc['user'], 
                         desc,
                         shelved ))
        
        return log


    def _fetch_shelved_logs(self, userid):
        try:
            # Shelved changes. Note: those have a key 'shelved': ''
            changes = self.p4.run_changes('-l', '-s', 'shelved', '-u', userid)
            return self._get_log_changelists(changes)
        except Exception, e:
            raise SCMError('Error fetching revisions: ' +str(e))


    def _fetch_log_of_day_uncached(self, day):  
        try:
            # Fetch submitted changes
            day_plus_one = day + timedelta(days=1)
            changes = self.p4.run_changes('-l', '-s', 'submitted', '@' + day.strftime("%Y/%m/%d") + ',' + day_plus_one.strftime("%Y/%m/%d"))
            return self._get_log_changelists(changes)
        except Exception, e:
            raise SCMError('Error fetching revisions: ' +str(e))

        

class PerforcePostCommitTrackerTool(PerforcePostCommitTool):
    name = "Perforce Post Commit Tracker"
    
    freshness_delta = timedelta(days=21)
    
    def __init__(self, repository):
        PerforcePostCommitTool.__init__(self, repository)
        self.revisionCache = RepositoryRevisionCache('perforce_post_tracker.'+ urllib.quote(str(self.repository.path)), 
                                                     self.freshness_delta)

    @staticmethod
    def _create_client(path, username, password):
        if path.startswith('stunnel:'):
            path = path[8:]
            use_stunnel = True
        else:
            use_stunnel = False
        return PerforcePostCommitTrackerClient(path, username, password, use_stunnel)

    def get_fields(self):
        fields = PerforcePostCommitTool.get_fields(self)
        fields.append('scm_user')
        fields.append('revisions_choice')
        return fields

    def get_scm_user(self, userid):
        return self.revisionCache.get_scm_user(userid)     

    def set_scm_user(self, userid, scm_user):
        self.revisionCache.set_scm_user(userid, scm_user)     
 
    def get_missing_revisions(self, userid, scm_user):
        scm_user = scm_user or userid
        scm_user = scm_user.lower()
        return self.client.get_missing_revisions(userid, scm_user, self.revisionCache)
    
    def ignore_revisions(self, userid, new_revisions_to_be_ignored):
        self.revisionCache.ignore_revisions(userid, new_revisions_to_be_ignored)