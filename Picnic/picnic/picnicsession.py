from pyramid.view import view_config
import os,sys
import json
from pyramid.httpexceptions import HTTPNotFound, HTTPTemporaryRedirect
from webob import Response
from datetime import datetime,timedelta
import time
import resource
import multiprocessing
import copy
import stat
import traceback
import logging
import dplkit.role.decorator
log = logging.getLogger(__name__)

json_dateformat='%Y.%m.%dT%H:%M:%S'

@dplkit.role.decorator.exposes_attrs_of_field('framestream')
class PicnicProgressNarrator(object):
    def __init__(self,framestream,getLastValue,firstval,lastval,session):
        self.framestream=framestream
        #if hasattr(framestream,'provides'):
        #self.provides=framestream.provides
        self.getLastValue=getLastValue
        self.firstval=firstval
        self.lastval=lastval
        self.session=session
        updateSessionComment(self.session,content={'percentcomplete':0.0,'percentupdated':datetime.strftime(datetime.utcnow(),json_dateformat)})

    def __iter__(self):
        for f in self.framestream:
            try:
                lv=self.getLastValue(f)
                if isinstance(lv,datetime):
                    percent=100.0*((lv-self.firstval).total_seconds()/(self.lastval-self.firstval).total_seconds())
                else:
                    percent=100.0*((lv-self.firstval)/(self.lastval-self.firstval))
                updateSessionComment(self.session,content={'percentcomplete':percent,'percentupdated':datetime.strftime(datetime.utcnow(),json_dateformat)})
            except:
                print "Picnic Progress Narrator couldn't update percentcomplete"
                #raise
            yield f



class PicnicTaskWrapper:
    def __init__(self,sessionid):
        self.__task=None
        self.expiretime=datetime.utcnow()
        self.__exitcode=None
        self.sessionid=sessionid

    #def __repr__(self):
    def __updatesession(self):
        tses=loadsession(self.sessionid)
        tses['rescode']=self.__exitcode
        storesession(tses)


    def new_task(self,sessionid,*args, **kwargs):
        self.terminate()
        self.sessionid=sessionid
        log.debug("starting args = (%s) kw_args = (%s)"% (args, kwargs))
        self.__task=multiprocessing.Process(*args, **kwargs)

    def is_alive(self):
        return self.__task!=None and self.__task.is_alive()

    def terminate(self):
        if self.is_alive():
            self.__task.terminate()
            self.__exitcode='terminated'
            self.__updatesession()

    def task(self):
        return self.__task

    def join(self):
        if self.is_alive():
            self.__task.join()
            self.exitcode()
            self.__task=None

    def start(self,*args, **kwargs):
        if self.__task==None:
            return None
        self.updateExpireTime(*args, **kwargs)
        return self.__task.start()

    def exitcode(self):
        if self.is_alive():
            self.__exitcode=None
        elif self.__task!=None:
            self.__exitcode=self.__task.exitcode
            self.__updatesession()
            self.__task=None
        return self.__exitcode

    def checkExpireTime(self,now=datetime.utcnow()):
        if self.expiretime<=now:
            self.terminate()

    def updateExpireTime(self,updateseconds=30,now=datetime.utcnow(),force_time=None):
        if force_time!=None:
            self.expiretime=force_time
        else:
            self.expiretime=now+timedelta(seconds=updateseconds)


tasks={}
#imagepathcacheage=None

def safejoin(*args):
    tmp=os.path.abspath(args[0])
    tmpargs=[tmp]
    if len(args)>1:
        tmpargs.extend(args[1:])
    ret=os.path.join(*tmpargs)
    if not ret.startswith(tmpargs[0]):
        print "path " + ret + " doesn't start with " + tmpargs[0]
        return None
    if not ret.endswith(tmpargs[-1]):
        print "path " + ret + " doesn't end with " + tmpargs[-1]
        return None
    if len(tmpargs)>2:
        for p in tmpargs[1:(len(tmpargs)-1)]:
            if os.path.sep+p+os.path.sep not in ret:
                print "path " + ret + " doesn't with " + p
                return None
    return ret


def sessionActive(sessionid):
    if not isinstance(sessionid,basestring):
        sessionid = sessionid['sessionid']
    return sessionid in tasks and tasks[sessionid].is_alive()


# requests take 4 stages:
# - fork/dispatch: sets up session, logfile, forks, and calls specific function
# - parse parameters
# - construct DPL
# - execute

dispatched=False
dispatchers={}

def addDispatchers(d):
    dispatchers.update(d)
    try:
        os.mkdir(_sessionfolder(None))
    except:
        pass

def taskdispatch(dispatcher,request,session,logstream=None):
    if logstream!=None:
        os.dup2(logstream.fileno(),sys.stdout.fileno())
        os.dup2(logstream.fileno(),sys.stderr.fileno())
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    ress=[]
    ress.append(resource.RLIMIT_DATA)
    #ress.append(resource.RLIMIT_STACK)
    ress.append(resource.RLIMIT_AS)
    resssize=16*1024*1024*1024
    for res in ress:
        resource.setrlimit(res,(resssize,resssize))
    starttime=datetime.utcnow()
    print 'started ',starttime
    try:
        dispatchers[dispatcher](request,session,isBackground=(None if logstream==None else True))
    except Exception,e:
        updateSessionComment(loadsession(session['sessionid']),'ERROR- %s :%s' % (type(e).__name__,e))
        print traceback.format_exc()
    print 'finished ',datetime.utcnow(),'(',(datetime.utcnow()-starttime).total_seconds(),'seconds )'
 
def sessionfile(sessionid,filename,create=False):
    if not isinstance(sessionid,basestring):
        sessionid = sessionid['sessionid']
    fold=_sessionfolder(sessionid)
    if create and not os.access(fold,os.R_OK):
        os.mkdir(fold)
    if filename==None:
        return fold
    return safejoin(fold,filename)

def _sessionfolder(sessionid):
    if sessionid==None:
        return os.getenv('SESSIONFOLDER',safejoin('.','sessions'))
    return safejoin(_sessionfolder(None),sessionid);

def loadjson(session,filename,**kwargs):
    if isinstance(session,basestring):
        sessionid=session
    else:
        sessionid=session['sessionid']
    try:
        return json.load(file(sessionfile(sessionid,filename)))
    except IOError:
        if 'failvalue' in kwargs:
            return kwargs['failvalue']
        raise

def loadsession(sessionid):
    retry=5;
    ret=None
    while ret==None:
        try:
            ret=loadjson(sessionid,'session.json')
        except:
            retry-=1
            if retry==0:
                raise
            time.sleep(.2)
    return ret

def storejson(session,d,filename):
    if isinstance(session,basestring):
        sessionid=session
    else:
        sessionid=session['sessionid']
    json.dump(d,file(sessionfile(sessionid,filename,create=True),'w'),indent=4,separators=(',', ': '))

def storesession(session):
    storejson(session,session,'session.json')

def updateSessionComment(sessionid,value=None,k='comment',content=None):
    if isinstance(sessionid,basestring):
        print 'WARNING: Loading session, rather than using session directly. Practice is to use the same dictionary in the lone process'
        session = loadsession(sessionid)
    else:
        session = sessionid
    if content!=None:
        session.update(content)
        print datetime.utcnow(),' Updating Session',content
    else:
        session[k]=value
        print datetime.utcnow(),' Updating Session',k,':',value
    storesession(session)

def newSessionProcess(dispatch,request,session,*args,**kwargs):
    storesession(session)
    sessionid=session['sessionid']
    logfilepath=sessionfile(sessionid,'logfile',create=True)
    if sessionid in tasks and tasks[sessionid].is_alive():
        log.debug( 'cancelling stale task for  %s' %sessionid)
        tasks[sessionid].terminate()
        tasks[sessionid].join()
        del tasks[sessionid]
    else:
        tasks[sessionid]=PicnicTaskWrapper(sessionid=sessionid)
        log.debug("newSessionProcess - created task %s for %s" % (repr(tasks[sessionid]), sessionid))
    session['rescode']=''
    folder=_sessionfolder(sessionid)
    for s in os.listdir(folder):
        if s.startswith('.') or s=='logfile' or s.endswith('.json') or s.endswith('.nc') or s.endswith('.cdl'):
            continue
        os.unlink(safejoin(folder,s))
    stdt=file(logfilepath,'w')
    tasks[sessionid].new_task(sessionid=sessionid,target=taskdispatch,args=(dispatch,request,session,stdt))
    session['comment']='inited'
    session['percentcomplete']=0.0
    session['task_started']=datetime.strftime(datetime.utcnow(),json_dateformat)
    session['logfileurl']= request.route_path('session_resource',session=sessionid,filename='logfile')
    dispatchers[dispatch](request,session,False)
    storesession(session)
    if haveUserTracking():
        import cgi_datauser
        if datacookiename in request.cookies:
            userid=request.cookies[datacookiename]
        #elif datacookiename in request.params:
        #    userid=request.params.getone(datacookiename)
        else:
            userid="unknown"

        b=cgi_datauser.lidarwebdb()

        processDescription={
            'taskid':sessionid,
            'uid':userid,
            'processtype':dispatch,
            'start_time':datetime.strptime(session['starttime'],json_dateformat).strftime('%F %T'),
            'end_time':datetime.strptime(session['endtime'],json_dateformat).strftime('%F %T'),
            'min_alt':session['altmin'],
            'max_alt':session['altmax'],
            }
        if 'timeres' in session:
            processDescription['timeres']=session['timeres'] 
        if 'altres' in session:
            processDescription['altres']=session['altres']
        if 'process_control' in session:
            processDescription['parameterstructure']=json.dumps(session['process_control'],separators=(',', ':'))
        elif os.access(sessionfile(session,'process_parameters.json'),os.R_OK):
            processDescription['parameterstructure']=json.dumps(loadjson(session,'process_parameters.json'),separators=(',', ':'))
        dropfields=['process_control' ,'display_defaults','logfileurl']
        omitf=[ 'selected_fields' ,'figstocapture']
        needcopy=False
        for f in dropfields+omitf:
            if f in session:
                needcopy=True
                break
        if needcopy:
            tsess=copy.deepcopy(session)
            for f in dropfields:
                if f in session:
                    del tsess[f]
            for f in omitf:
                if f in session:
                    tsess[f]='omitted'
        else:
            tsess=session
        processDescription['commandline']=json.dumps(tsess,separators=(',', ':')),

        b.addProcess(processDescription)

    log.debug('starting task for %s dispatch named %s (mypid = %d)' %(sessionid, dispatch,os.getpid() ) )
    tasks[sessionid].start(updateseconds=120,*args,**kwargs)
    stdt.close()
    return HTTPTemporaryRedirect(location=makeUserCheckURL(request,request.route_path('progress_withid',session=sessionid)))

def moddateoffile(f):
    return os.stat(f).st_mtime

@view_config(route_name='session_resource')
def session_resource(request):
    fn=request.matchdict['filename']
    if 'session' in request.matchdict:
        f=sessionfile(request.matchdict['session'],fn)
    else:
        return HTTPNotFound("File doesn't exist")
       
    m=None
    if not os.access(f,os.R_OK):
        return HTTPNotFound("File doesn't exist")
    if fn.endswith('.jpg'):
        m='image/jpeg'
    if fn.endswith('.png'):
        m='image/png'
    if fn=='logfile' or fn.endswith('.txt') or fn.endswith('.cdl'):
        m='text/plain'
    if fn.endswith('.json'):
        m='application/json'
    #if fn.endswith('.cdl'):
    #    m='application/x-netcdf'
    if fn.endswith('.nc') or fn.endswith('.cdf'):
        m='application/x-netcdf'

    if m==None:
        return HTTPNotFound("File inaccessible")
    return Response(content_type=m,app_iter=file(f))

 
    
@view_config(route_name='progress')
def progress_getlastid(request):
    sessionid=request.session.get_csrf_token()  #get_cookies['session']#POST['csrf_token']
    return HTTPTemporaryRedirect(location=request.route_path("progress_withid",session=sessionid))
    
@view_config(route_name='progress_withid',renderer='templates/progress.pt')
def progresspage(request):
    #print 'URLREQ: ',request.matched_route.name
    sessionid=request.matchdict['session']#session.get_csrf_token()  #get_cookies['session']#POST['csrf_token']
    #check status of this task
    #if sessionid not in tasks:
    #    return HTTPNotFound('Invalid session')
    session=None
    retry=10
    while session==None and retry>0:
        try:
            session=loadsession(sessionid)
        except:
            time.sleep(.05)
            session=None
            retry=retry-1
    if session==None:
        return HTTPTemporaryRedirect(location=request.route_path('progress_withid',session=sessionid))

    if sessionid in tasks and tasks[sessionid].is_alive():
        #load intermediate if not
        now=datetime.utcnow()
        tasks[sessionid].updateExpireTime(now=now)

        for sesid in tasks:
            if tasks[sesid].is_alive():
                tasks[sesid].checkExpireTime(now=now)
        for timefield in ['starttime','endtime','task_started','percentupdated']:
            if timefield in session:
                session[timefield]=datetime.strptime(session[timefield],json_dateformat)

        return {'pagename':session['name'],'progresspage':request.route_path('progress_withid',session=sessionid),
            'sessionid':sessionid,'destination':session['finalpage'],'session':session,'datetime':datetime,'timedelta':timedelta}
    #load next page if complete
    if sessionid in tasks:
        rescode=tasks[sessionid].exitcode()
    else:
        rescode='Unknown (old task)'
    log.debug('finished task for  %s with result code %s (mypid = %d)' % ( sessionid, rescode, os.getpid()) )
    return HTTPTemporaryRedirect(location=session['finalpage'])


#### STATUS bits
def printNumber(fn,fmt,base,orders):
    powr=1.0
    order=None
    for o in range(0,len(orders)):
        if fn<(powr*base):
            order=o
            break
        powr*=base
    if order==None:
        order=-1
    stri=fmt+' %s '
    return stri % (fn/(powr),orders[order])

def infoOfFile(fn):
    tmp=os.stat(fn)
    return (datetime.utcfromtimestamp(tmp.st_ctime),datetime.utcfromtimestamp(tmp.st_mtime),tmp.st_size,tmp.st_mode)

def getSizeOfFolder(folder):
    ret=0
    for fn in os.listdir(folder):
        if fn.startswith('.'):
            continue
        inf=infoOfFile(safejoin(folder,fn))
        if stat.S_ISDIR(inf[3]):
            ret+=getSizeOfFolder(safejoin(folder,fn))
        elif stat.S_ISREG(inf[3]):
            ret+=inf[2]
    return ret

def sessinfo_gen(folder,sessTimes):
    for inf in sessTimes:
        n=inf['sessionid']
        try:
            sessinfo=loadsession(n)
        except:
            sessinfo=None
        yield {
            'sessionid':n,
            'startTime':infoOfFile(safejoin(folder,n))[0],
            'running':tasks[n].is_alive() if n in tasks and tasks[n]!=None else False,
            'task':tasks[n].task() if n in tasks else None,
            'session':sessinfo,
            'size':getSizeOfFolder(safejoin(folder,n)),
            }

def __removeSession(sess):
    sesf=_sessionfolder(sess)
    fs=os.listdir(sesf)
    for f in fs:
        os.unlink(safejoin(sesf,f))
        print 'unlinked ',safejoin(sesf,f)
    os.rmdir(sesf)
    print 'unlinked ',sesf

from operator import itemgetter
            
@view_config(route_name='status',renderer='templates/status.pt')
def statuspage(request):
    folder=_sessionfolder(None)#safejoin('.','sessions');
    _sess=os.listdir(folder)
    sess=[]
    for s in _sess:
        if s.startswith('.') or not os.path.isdir(safejoin(folder,s)):
            continue
        sess.append(s)
    sessTimes=[{'sessionid':n,'startTime':infoOfFile(safejoin(folder,n))[0]}  for n in sess]
    sessTimes.sort(key=itemgetter('startTime'),reverse=True)
    if 'purgeone' in request.params:
        purge=request.params.getone('purgeone')
        if purge in sess:
            __removeSession(purge)
            return HTTPTemporaryRedirect(location=request.current_route_path())
    if 'purge' in request.params:
        purgefrom=request.params.getone('purge')
        found=False
        for inf in sessTimes:#(sessid,sdate,running,task) in sessinfo:
            if inf['sessionid']==purgefrom:
                found=True
                continue
            if found:
                __removeSession(inf['sessionid'])
        if found:
            return HTTPTemporaryRedirect(location=request.current_route_path())
    if 'terminate' in request.params:
        terminate=request.params.getone('terminate')
        found=False
        for sessid in sess:
            if sessid==terminate:
                log.debug('will try to terminate %s'% sessid)
                if sessid in tasks and tasks[sessid].is_alive():
                    tasks[sessid].terminate()
                    tasks[sessid].join()
                    del tasks[sessid]
                    return HTTPTemporaryRedirect(location=request.current_route_path())
                break
    runningtasks=0
    for ses in tasks:
            if tasks[ses].is_alive():
                runningtasks=runningtasks+1
            else:
                tasks[ses].exitcode()#update the session exitcode

    return {'sessions':sess,
            'sessioninfo':sessinfo_gen(folder,sessTimes),
            'runningtasks':runningtasks,
            'datetime':datetime,'timedelta':timedelta,'printNumber':printNumber}

@view_config(route_name='debug',renderer='templates/debug.pt')
def debugpage(request):
    info=[]
    info.append({'name':'Python','content':sys.executable})
    info.append({'name':'Python Version','content':sys.version})
    info.append({'name':'Python platform','content':sys.platform})
    info.append({'name':'Python Path','content':os.getenv('PYTHONPATH',"")})
    return {'simpleinfos':info}

def filelistinfo_gen(folder,filelist):
    for f in filelist:
        inf=infoOfFile(safejoin(folder,f))
        yield {'name':f,'stats':[printNumber(inf[2],'%.2f',1024,['bytes','KB','MB','GB']),inf[0],inf[1]]}

@view_config(route_name='debugsession',renderer='templates/debugsession.pt')
def debugsession(request):
    sessionid=request.matchdict['session']
    folder=_sessionfolder(sessionid);
    try:
        session=loadsession(sessionid)
    except:
        session=None
    if sessionid in tasks:
        task=tasks[sessionid]
        running=task.is_alive()
        if not running:
            try:
                tses=loadsession(sessionid)
                if 'rescode' not in tses or tses['rescode']=='':
                    tses['rescode']=task.exitcode()
                    storesession(tses)
            except:
                pass
    else:
        task=None
        running=False
    if os.access(folder,os.R_OK):
        filelist=os.listdir(folder)
        filelist.sort()
        filelistinfo=filelistinfo_gen(folder,filelist)
    else:
        filelist=None
        filelistinfo=None
    if 'session.json' in filelist:
        try:
            session=loadsession(sessionid)
        except:
            session=None
    else:
        session=None
    return {'task':task.task() if task!=None else None,
            'files':filelist,
            'fileinfo':filelistinfo,
            'session':session,
            'running':running,
            'sessionid':sessionid,'printNumber':printNumber}


def isValidEmailAddress(stringval):
    s=stringval.split('@')
    if len(s)!=2:
        return False
    return True

datacookiename="datauser"
keyfield="email"
reqfields=("email","name",)
optionalfields=("org",)
doHaveUserTracking=None

def haveUserTracking():
    global doHaveUserTracking
    if doHaveUserTracking==None:
        if os.getenv("PICNIC_USERCHECK",None)!=None:
            doHaveUserTracking=(os.getenv("PICNIC_USERCHECK")=='true')
        else:
            try:
                import cgi_datauser
                doHaveUserTracking=True
            except:
                doHaveUserTracking=False
    return doHaveUserTracking

def makeUserCheckURL(request,destination,destparms=None):#only works with a simple destination
    if not haveUserTracking():
        return destination
    returl=request.route_path('userCheck')+'?URL='+destination
    parms={}
    if destparms!=None:
        parms.update(destparms)
    for f in reqfields+optionalfields:
        if f in request.params:
            parms[f]=request.params.getone(f)
    if len(parms)>0:
        returl+='&' + '&'.join([(f+'='+parms[f]) for f in parms])
    return returl

def getFirst(req,val,deflt=None):
    for r in (req.POST,req.GET):
        if val in r:
            v=r.getone(val)
            if len(v)>0:
                return v
    return deflt

@view_config(route_name='userCheck',renderer='templates/userCheck.pt')
def userCheck(request):
    #print 'URLREQ: ',request.matched_route.name
    req=request
    try:
        import cgi_datauser
    except:
        print "Couldn't load cgi_datauser from hsrl git codebase. user tracking disabled"
        jumpurl=''
        parms='?'+getFirst(req,'PARAMS','')
        if len(parms)<=1:
            parms='?'+request.query_string#os.environ.get("QUERY_STRING","");
        jumpurl=getFirst(req,'URL','')
        if len(jumpurl)<=0:
            jumpurl='/'
            parms=''
        dest=jumpurl + parms
        return HTTPTemporaryRedirect(location=dest)
    dbc=cgi_datauser.lidarwebdb()
    info={};
    doForm=True
    fromSQL=False
    indebug=False #True
    if len(getFirst(req,keyfield,''))>0 or datacookiename in request.cookies or indebug:#fixme maybe not read cookie here, just grab from form
        doForm=False
        if len(getFirst(req,keyfield,''))>0:
            keyv=getFirst(req,keyfield)
            if not isValidEmailAddress(keyv):
                doForm=True
            else:
                info[keyfield]=keyv
                hasreq=True;
                for f in reqfields:
                    if len(getFirst(req,f,''))>0:
                        info[f]=getFirst(req,f)
                    else:
                        hasreq=False
                for f in optionalfields:
                    if len(getFirst(req,f,''))>0:
                        info[f]=getFirst(req,f)
                if not hasreq:#work by lookup
                    ti=dbc.getUserByEMail(info[keyfield])
                    if ti:
                        info=ti
                        fromSQL=True
        elif datacookiename in request.cookies:
            ti=dbc.getUserByUID(request.cookies[datacookiename])
            if ti:
                info=ti
                fromSQL=True
        elif indebug: #DEBUG ONLY
            ti=dbc.getUserByEMail("null")
            if ti==None:
                dbc.addClient({'email':'null','name':'bubba'})
                ti=dbc.getUserByEMail("null")
            info=ti
            fromSQL=True
        for f in reqfields:
            if not info.has_key(f):
                doForm=True
                break
    if not doForm:
        #print 'Not doing form'
        if not fromSQL:
            #print 'Not from SQL'
            uid=dbc.addClient(info)
        else:
            #print 'From SQL'
            uid=info['uid']
        if uid!=None:
            #print 'have UID'
            parms=''
            jumpurl=''
            parms='?'+getFirst(req,"PARAMS",'')
            if len(parms)<=1:
                parms='?'+request.query_string#os.environ.get("QUERY_STRING","");
            jumpurl=getFirst(req,"URL","")
            if len(jumpurl)<=0:
                jumpurl='/'
                parms=''
            dest=jumpurl + parms
            if False and indebug:
                print "Content-Type: text/plain"
                print
                if len(cookies)>0:
                    print cookies
                else:
                    print "No cookies"
                print "jump to %s" % dest
            else:
                bod="""
                <HTML><HEAD>
                <META HTTP-EQUIV="Refresh" CONTENT="0;url=%s">
                <TITLE>Registered</TITLE>
                </HEAD><BODY></BODY></HTML>
               """  % dest
            resp = Response(body=bod,content_type="text/html")
            resp.set_cookie(datacookiename,uid,max_age=timedelta(weeks=12))
            #print 'done. returning forward to',dest
            return resp
    #print 'Doing form'
    #form
    #info=dbc.getUserByEMail("null")
    #print "Content-Type: text/html"
    #if len(cookies)>0:
    #    print cookies
    #print
    info["URL"]=getFirst(req,"URL","")
    info['PARAMS']=getFirst(req,'PARAMS',request.query_string)
    info["MYURL"]=request.path#os.environ.get("SCRIPT_NAME","")
    #print 'form ops are',info

    fields=("email","name","org");
    fielddesc={"email":"E-Mail Address",
               "name":"Name",
               "org":"Organization"}

    return { 'MYURL': info['MYURL'],
             'URL': info['URL'],
             'PARAMS': info['PARAMS'],
             'fields': fields,
             'info': info,
             'fielddesc': fielddesc,
             'reqfields': reqfields}
