import base64
import cgi
import logging
import os

import datetime
import urllib

from google.appengine.api import urlfetch
from google.appengine.api.labs import taskqueue
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app

from django.utils import simplejson

twitteruser = ''     # The user account that queries twitter.com
twitterpassword = '' # The password for the user account.

class CacheEntry(db.Model):
        sn = db.StringProperty(required=True)
        last_modified = db.DateTimeProperty(required=True)
        mime_type = db.StringProperty(required=True)
class CacheEntryValue(db.Model):
        value = db.BlobProperty(required=True)

seconds_to_keep = 86400
def log_headers(request):
        headers = ['Accept', 'Origin']
        for header in headers:
                if header in request.headers:
                        logging.debug('%s: %s' % (header, request.headers[header]))

def get_entry(sn, mime_type):
        modified_date = datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds_to_keep)
        return CacheEntry.all().filter('sn =', sn).filter('mime_type =', mime_type).filter('last_modified >', modified_date).get()

def create_entry(sn, mime_type, value):
        try:
                def tran(sn, mime_type, value):
                        entry = CacheEntry(sn=sn, last_modified=datetime.datetime.utcnow(), mime_type=mime_type)
                        entry.put()
                        value = CacheEntryValue(parent=entry, key_name='value', value=value)
                        value.put()
                db.run_in_transaction(tran, sn, mime_type, value)
        except:
                logging.error('Could not create cache entry for sn=%s, mime_type=%s' % (sn, mime_type))

class ID(webapp.RequestHandler):

	def get(self, sn):
		acceptType = self.request.accept.best_match(['application/rdf+xml', 'text/turtle', 'text/n3', 'text/html'])
                log_headers(self.request)

		if acceptType == 'application/rdf+xml' or acceptType == 'text/n3' or acceptType == 'text/turtle':
			self.response.set_status(303)
			self.response.headers['Location'] = 'http://twitter2foaf.appspot.com/data/' + sn
		else:
			self.response.set_status(303)
			self.response.headers['Location'] = 'http://twitter2foaf.appspot.com/user/' + sn

class User(webapp.RequestHandler):

        def get(self, sn):
                log_headers(self.request)
		
                template_values = {'username': sn}
                template_name = 'foaf.html'
                path = os.path.join(os.path.dirname(__file__), template_name)
                self.response.out.write(template.render(path, template_values))

class Data(webapp.RequestHandler):

	def get(self, sn):
                accept_type = self.request.accept.best_match(['application/rdf+xml', 'text/turtle', 'text/n3'])
                if accept_type == 'text/turtle':
                        accept_type = 'text/n3'

                log_headers(self.request)
                                      
                entry = None
                cursor = self.request.get('cursor', '-1')
                if cursor == '-1':
                        try:
                                entry = get_entry(sn, accept_type)
                        except:
                                logging.error('Failed to get entry for screen name %s' % sn)
                                entry = None

                if entry == None:
                        logging.info('Cache miss for sn=%s, accept_type=%s' % (sn, accept_type))
                        outresult = self.get_uncached(sn, accept_type)
                        if outresult <> None:
                                logging.info('Caching for sn=%s, accept_type=%s' % (sn, accept_type))
                                create_entry(sn, accept_type, outresult.getvalue())

                else:
                        logging.info('Cache hit for sn=%s, accept_type=%s' % (sn, accept_type))
                        value = CacheEntryValue.get_by_key_name('value', parent=entry)
                        self.response.headers['Content-Type'] = entry.mime_type
                        self.response.headers['Last-Modified'] = entry.last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
                        age = datetime.datetime.utcnow() - entry.last_modified
                        time_left = max(seconds_to_keep - age.seconds, 0)
                        self.response.headers['Cache-Control'] = 'public, max-age=' + str(time_left)
                        self.response.out.write(value.value)

        def get_uncached(self, sn, acceptType):
                cursor = self.request.get('cursor', '-1')

                headers = {'Authorization': 'Basic ' + base64.encodestring(twitteruser + ':' + twitterpassword).strip()}
                rpcKnows = urlfetch.create_rpc()
                urlfetch.make_fetch_call(rpcKnows, "https://twitter.com/statuses/friends.json?screen_name=" + sn + "&cursor=" + cursor, headers=headers)
                rpcPerson = urlfetch.create_rpc()
                urlfetch.make_fetch_call(rpcPerson, "https://twitter.com/users/show.json?screen_name=" + sn, headers=headers)

                try:                

                        resultKnows = rpcKnows.get_result()
                        resultPerson = rpcPerson.get_result()

                        headers = ['X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset']
                        for header in headers:
                                if header in resultKnows.headers:
                                        logging.info(header + ': ' + resultKnows.headers[header])
                                if header in resultPerson.headers:
                                        logging.info(header + ': ' + resultPerson.headers[header])

                        logging.info('status_code (statuses/friends): %i' % resultKnows.status_code)
                        logging.info('status_code (users/show): %i' % resultPerson.status_code)
                        if (resultKnows.status_code == 200 or resultKnows.status_code == 401) and resultPerson.status_code == 200:

                                # for 401, then we just don't include any 'knows', because who the person knows
                                #   is protected.
                                knows = []
                                if resultKnows.status_code == 200:
                                        # but for 200, load who the person knows.
                                        dataKnows = simplejson.loads(resultKnows.content)
                                        knows = [
                                                        { 
                                                                'name': friend['name'],
                                                                'screen_name': friend['screen_name'],
                                                                'homepage': friend['url'],
                                                                'depiction': friend['profile_image_url'],
                                                        }
                                                        for friend in dataKnows['users'] ]
                                
                                dataPerson = simplejson.loads(resultPerson.content)
                                template_values = {
                                                'name': dataPerson['name'],
                                                'screen_name': dataPerson['screen_name'],
                                                'homepage': dataPerson['url'],
                                                'depiction': dataPerson['profile_image_url'],
                                                'knows': knows,
                                        }
                                if resultKnows.status_code == 200:
                                        if dataKnows['previous_cursor'] <> 0:
                                                template_values['previous_cursor'] = dataKnows['previous_cursor']
                                        if dataKnows['next_cursor'] <> 0:
                                                template_values['next_cursor'] = dataKnows['next_cursor']

                                template_name = 'foaf.rdf'
                                if acceptType == 'text/n3':
                                        self.response.headers['Content-Type'] = 'text/n3;charset=utf-8'
                                        template_name = 'foaf.n3'
                                else:
                                        self.response.headers['Content-Type'] = 'application/rdf+xml;charset=utf-8'
                                        template_name = 'foaf.rdf'
                                self.response.headers['Last-Modified'] = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
                                self.response.headers['Cache-Control'] = 'public, max-age=' + str(seconds_to_keep)
                                path = os.path.join(os.path.dirname(__file__), template_name)
                                self.response.out.write(template.render(path, template_values))

                                return self.response.out
                except urlfetch.DownloadError:
                        logging.warning('Download error')

		self.response.set_status(504)
		self.response.headers['Content-Type'] = 'text/plain'
		self.response.out.write("Timed out\n")

		return None

class Delete(webapp.RequestHandler):
        def get(self):
                fetch_count = self.request.get('count', '10')
                taskqueue.add(url='/delete', params={'count': fetch_count})
        def post(self):
                fetch_count = int(self.request.get('count', '10'))
                if fetch_count < 1:
                        fetch_count = 1
                logging.debug('fetch_count = %i', fetch_count)
                modified_date = datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds_to_keep)
                entries = CacheEntry.all().filter('last_modified <', modified_date).fetch(fetch_count)

                entities_to_delete = []
                for entry in entries:
                        value = CacheEntryValue.get_by_key_name('value', parent=entry)
                        entities_to_delete.append(value)
                        entities_to_delete.append(entry)

                number_entities_to_delete = len(entities_to_delete)
                if number_entities_to_delete == 0:                        
                        logging.debug('Deleting %i entities' % number_entities_to_delete)
                else:
                        logging.info('Deleting %i entities' % number_entities_to_delete)

                enqueue_another_task = False
                if len(entries) == fetch_count:
                        enqueue_another_task = True

                if fetch_count < 1:
                        fetch_count = 1

                try:
                        db.delete(entities_to_delete)
                except:
                        enqueue_another_task = True
                        fetch_count = fetch_count - 1

                self.response.out.write(str(number_entities_to_delete))

                if enqueue_another_task:
                        taskqueue.add(url='/delete', params={'count': fetch_count})

application = webapp.WSGIApplication(
	[
		('/id/([^/]+)', ID),
		('/data/([^/]+)', Data),
		('/user/([^/]+)', User),                
                ('/delete', Delete),
	],
	debug=True)

def main():
	run_wsgi_app(application)

if __name__ == "__main__":
	main()
